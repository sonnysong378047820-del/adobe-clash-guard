<#
.SYNOPSIS
    把设计文件的双击关联接到 AdobeClashGuard。

.DESCRIPTION
    AdobeClashGuard 只改写 .lnk 快捷方式，双击文件走的是另一条路径（注册表 ProgId），
    因此不受保护。本脚本把 .ai / .psd / .aep / .eps 的打开命令改写成
    "AdobeClashGuard.exe --launch <key> \"%1\""，让双击文件同样先经过启动检查。

    所有写入都在 HKCU\Software\Classes 下（用户级），不需要管理员权限，
    不修改 UserChoice（避免触发 Windows 的默认应用防篡改校验）。

    同时把 AdobeClashGuard.exe 注册成一个系统"已知应用"
    （HKCU\Software\Classes\Applications），否则 Explorer 双击时会因为
    "认不出这是哪个应用"而弹出「选择一个应用」对话框。

.PARAMETER Undo
    撤销本脚本写入的全部内容，恢复到执行前的状态。

.PARAMETER Extensions
    要接入的扩展名，默认 .ai,.psd,.aep,.eps

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_file_associations.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_file_associations.ps1 -Undo
#>
[CmdletBinding()]
param(
    [switch]$Undo,
    [string[]]$Extensions = @('.ai', '.psd', '.aep', '.eps')
)

$ErrorActionPreference = 'Stop'

$GuardDir  = Join-Path $env:LOCALAPPDATA 'AdobeClashGuard\v2.1'
$GuardExe  = Join-Path $GuardDir 'AdobeClashGuard.exe'
$StateFile = Join-Path $GuardDir 'state.json'

$ClassesKey = 'HKCU:\Software\Classes'
$BackupKey  = Join-Path $ClassesKey 'AdobeClashGuard.Backup'
$AppKey     = Join-Path $ClassesKey 'Applications\AdobeClashGuard.exe'

# 扩展名 -> 目标进程文件名（用于在 state.json 里找对应的 key）
$ExtTarget = @{
    '.ai'  = 'illustrator.exe'
    '.eps' = 'illustrator.exe'
    '.psd' = 'photoshop.exe'
    '.aep' = 'afterfx.exe'
}

function Write-Step  { param($m) Write-Host "  $m" }
function Write-Ok    { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Skip  { param($m) Write-Host "  [跳过] $m" -ForegroundColor DarkGray }
function Write-Warn2 { param($m) Write-Host "  [注意] $m" -ForegroundColor Yellow }
function Write-Fail  { param($m) Write-Host "  [失败] $m" -ForegroundColor Red }

# --------------------------------------------------------------------------
# 撤销
# --------------------------------------------------------------------------
function Invoke-Undo {
    Write-Host ""
    Write-Host "撤销文件关联接入" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path $BackupKey)) {
        Write-Skip "没有找到本脚本的备份记录（$BackupKey），无需撤销。"
        return
    }

    foreach ($item in @(Get-ChildItem $BackupKey -ErrorAction SilentlyContinue)) {
        $progId = $item.PSChildName
        $cmdKey = Join-Path $ClassesKey "$progId\shell\open\command"
        $orig   = (Get-ItemProperty -Path $item.PSPath -ErrorAction SilentlyContinue).OriginalCommand
        $had    = (Get-ItemProperty -Path $item.PSPath -ErrorAction SilentlyContinue).HadOriginal

        if ($had -eq '1' -and $orig) {
            if (-not (Test-Path $cmdKey)) { New-Item -Path $cmdKey -Force | Out-Null }
            Set-ItemProperty -Path $cmdKey -Name '(default)' -Value $orig
            Write-Ok "已还原 $progId 的原始打开命令"
        }
        else {
            if (Test-Path $cmdKey) {
                Remove-Item -Path $cmdKey -Recurse -Force -ErrorAction SilentlyContinue
                Write-Ok "已移除 $progId 的接入命令"
            }
            # 顺手清掉本脚本建出来的空壳键
            $progKey = Join-Path $ClassesKey $progId
            if (Test-Path $progKey) {
                $left = @(Get-ChildItem $progKey -Recurse -ErrorAction SilentlyContinue |
                          Where-Object { $_.PSChildName -ne 'shell' })
                $hasCommand = Test-Path (Join-Path $progKey 'shell\open\command')
                if (-not $hasCommand) {
                    Remove-Item -Path $progKey -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
        Remove-Item -Path $item.PSPath -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path $AppKey) {
        Remove-Item -Path $AppKey -Recurse -Force
        Write-Ok "已移除 Applications 应用注册"
    }
    if (Test-Path $BackupKey) {
        Remove-Item -Path $BackupKey -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "撤销完成。双击行为已恢复为接入前的状态。" -ForegroundColor Green
}

# --------------------------------------------------------------------------
# 前置检查
# --------------------------------------------------------------------------
function Assert-Prereq {
    $problems = @()
    if (-not (Test-Path $GuardExe))  { $problems += "找不到 $GuardExe —— 请先安装（或升级）AdobeClashGuard" }
    if (-not (Test-Path $StateFile)) { $problems += "找不到 $StateFile —— 请先运行一次 AdobeClashGuard 完成图标接入" }
    if ($problems.Count -gt 0) {
        Write-Host ""
        foreach ($p in $problems) { Write-Fail $p }
        Write-Host ""
        exit 1
    }
}

# 读取 state.json，返回 "exe 文件名(小写) -> launch key"
function Get-GuardKeys {
    $state = Get-Content -Path $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $map = @{}
    foreach ($prop in $state.entries.PSObject.Properties) {
        $e = $prop.Value
        if ($e.kind -ne 'adobe') { continue }
        if ($e.state -eq 'restored') { continue }
        $exeName = [System.IO.Path]::GetFileName([string]$e.target).ToLowerInvariant()
        if (-not $map.ContainsKey($exeName)) { $map[$exeName] = $prop.Name }
    }
    return $map
}

# 找出某扩展名当前实际生效的 ProgId（先看 UserChoice，再退回 HKCR 默认值）
function Get-ProgId {
    param([string]$Ext)
    $uc = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$Ext\UserChoice"
    if (Test-Path $uc) {
        $v = (Get-ItemProperty -Path $uc -ErrorAction SilentlyContinue).ProgId
        if ($v) { return $v }
    }
    $extKey = "Registry::HKEY_CLASSES_ROOT\$Ext"
    if (Test-Path $extKey) {
        $v = (Get-ItemProperty -Path $extKey -ErrorAction SilentlyContinue).'(default)'
        if ($v) { return $v }
    }
    return $null
}

# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
if ($Undo) { Invoke-Undo; return }

Write-Host ""
Write-Host "AdobeClashGuard —— 文件关联接入" -ForegroundColor Cyan
Write-Host ""

Assert-Prereq

$keys = Get-GuardKeys
if ($keys.Count -eq 0) {
    Write-Fail "state.json 里没有可用的 Adobe 记录，请先运行 AdobeClashGuard 接入图标。"
    exit 1
}

Write-Step "检查器：$GuardExe"
Write-Step "已识别：$(($keys.Keys | Sort-Object) -join ', ')"
Write-Host ""

$done = 0; $skipped = 0; $failed = 0
$guardCmdPrefix = '"' + $GuardExe + '" --launch '

foreach ($ext in $Extensions) {
    $want = $ExtTarget[$ext]
    if (-not $want) { Write-Skip "$ext 不在支持列表内"; $skipped++; continue }

    if (-not $keys.ContainsKey($want)) {
        Write-Skip "$ext —— 没有找到 $want 的受管记录（可能未安装该应用）"
        $skipped++
        continue
    }
    $key = $keys[$want]

    $progId = Get-ProgId -Ext $ext
    if (-not $progId) {
        Write-Skip "$ext —— 系统里没有默认打开方式，无法接入（可在资源管理器里先设置一次）"
        $skipped++
        continue
    }

    $cmdKey = Join-Path $ClassesKey "$progId\shell\open\command"
    $newCmd = $guardCmdPrefix + $key + ' "%1"'

    try {
        # 备份原始命令（只备份一次，避免覆盖掉最初的原始值）
        $bkItem = Join-Path $BackupKey $progId
        if (-not (Test-Path $bkItem)) {
            New-Item -Path $bkItem -Force | Out-Null
            $old = $null
            if (Test-Path $cmdKey) {
                $old = (Get-ItemProperty -Path $cmdKey -ErrorAction SilentlyContinue).'(default)'
            }
            Set-ItemProperty -Path $bkItem -Name 'OriginalCommand' -Value ([string]$old)
            Set-ItemProperty -Path $bkItem -Name 'HadOriginal' -Value $(if ($old) { '1' } else { '0' })
            Set-ItemProperty -Path $bkItem -Name 'Extension' -Value $ext
        }

        if (-not (Test-Path $cmdKey)) { New-Item -Path $cmdKey -Force | Out-Null }
        Set-ItemProperty -Path $cmdKey -Name '(default)' -Value $newCmd

        $check = (Get-ItemProperty -Path $cmdKey).'(default)'
        if ($check -ne $newCmd) { throw "写入后校验不一致" }

        Write-Ok ("{0}  ->  {1}（ProgId: {2}）" -f $ext, $want, $progId)
        $done++
    }
    catch {
        Write-Fail ("{0} 写入失败：{1}" -f $ext, $_.Exception.Message)
        $failed++
    }
}

# --------------------------------------------------------------------------
# 注册成系统"已知应用"（不注册的话，Explorer 双击会弹「选择一个应用」）
# --------------------------------------------------------------------------
try {
    New-Item -Path $AppKey -Force | Out-Null
    Set-ItemProperty -Path $AppKey -Name '(default)'        -Value 'Adobe 启动检查'
    Set-ItemProperty -Path $AppKey -Name 'FriendlyAppName'  -Value 'Adobe 启动检查（先退 Clash）'
    Set-ItemProperty -Path $AppKey -Name 'ApplicationName'  -Value 'AdobeClashGuard'
    Set-ItemProperty -Path $AppKey -Name 'ApplicationCompany' -Value 'AdobeClashGuard'
    Set-ItemProperty -Path $AppKey -Name 'DefaultIcon'      -Value ($GuardExe + ',0')

    $supKey = Join-Path $AppKey 'SupportedTypes'
    New-Item -Path $supKey -Force | Out-Null
    foreach ($ext in $Extensions) {
        New-ItemProperty -Path $supKey -Name $ext -Value '' -PropertyType String -Force | Out-Null
    }

    $appCmdKey = Join-Path $AppKey 'shell\open\command'
    New-Item -Path $appCmdKey -Force | Out-Null
    $firstKey = $null
    foreach ($ext in $Extensions) {
        $want = $ExtTarget[$ext]
        if ($want -and $keys.ContainsKey($want)) { $firstKey = $keys[$want]; break }
    }
    if ($firstKey) {
        Set-ItemProperty -Path $appCmdKey -Name '(default)' -Value ($guardCmdPrefix + $firstKey + ' "%1"')
    }
    Write-Ok "已注册为系统应用（Applications\AdobeClashGuard.exe）"
}
catch {
    Write-Fail ("应用注册失败：{0}" -f $_.Exception.Message)
    Write-Warn2 "不注册也能用，但 Explorer 双击时可能弹出「选择一个应用」对话框。"
}

Write-Host ""
Write-Host ("完成：接入 {0} 项，跳过 {1} 项，失败 {2} 项" -f $done, $skipped, $failed) -ForegroundColor Green
Write-Host ""
Write-Host "验证方法：开着 Clash Verge，双击任意 .ai 文件 → 应弹出「请先退出 Clash Verge」。" -ForegroundColor Cyan
Write-Host "撤销方法：加 -Undo 参数重新运行本脚本。" -ForegroundColor Cyan
Write-Host ""
if ($failed -gt 0) { exit 1 }
