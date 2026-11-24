# RMM Monitor: снимок Windows-хоста.
# Запускается агентом MeshCentral (SYSTEM), печатает JSON между маркерами.
# Никаких секретов: ОС, CPU, RAM, диски, IP, процессы и браузеры с владельцами.
param([int]$Top = 25)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$B = '###SNAPSHOT-BEGIN###'
$E = '###SNAPSHOT-END###'
function C([double]$v) { if ($v -lt 0) { return 0.0 }; if ($v -gt 100) { return 100.0 }; return [math]::Round($v, 1) }

$snap = $null
try {
    $now = Get-Date
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $cpu = @(Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue) | Select-Object -First 1
    $perf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq '_Total' } | Select-Object -First 1
    $sys = Get-CimInstance Win32_PerfFormattedData_PerfOS_System -ErrorAction SilentlyContinue | Select-Object -First 1

    $tm = [math]::Round([double]$os.TotalVisibleMemorySize / 1024, 0)
    $fm = [math]::Round([double]$os.FreePhysicalMemory / 1024, 0)
    $um = [math]::Max(0, $tm - $fm)
    $mp = if ($tm -gt 0) { C (100 * $um / $tm) } else { 0.0 }
    $cp = if ($perf -and $null -ne $perf.PercentProcessorTime) { C ([double]$perf.PercentProcessorTime) }
          elseif ($cpu -and $null -ne $cpu.LoadPercentage) { C ([double]$cpu.LoadPercentage) } else { 0.0 }
    $q = if ($sys -and $null -ne $sys.ProcessorQueueLength) { [int]$sys.ProcessorQueueLength } else { 0 }
    $boot = [datetime]$os.LastBootUpTime
    $up = [int64]($now - $boot).TotalSeconds
    if ($up -lt 0) { $up = 0 }

    $disks = @()
    foreach ($d in @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue)) {
        if (-not $d.Size -or [double]$d.Size -le 0) { continue }
        $disks += [pscustomobject]@{
            mount = [string]$d.DeviceID; label = [string]$d.VolumeName
            total_gb = [math]::Round([double]$d.Size / 1GB, 1)
            free_gb = [math]::Round([double]$d.FreeSpace / 1GB, 1)
            used_pct = C (100 * (1 - [double]$d.FreeSpace / [double]$d.Size))
        }
    }

    $ips = @()
    try {
        foreach ($a in @([System.Net.Dns]::GetHostAddresses([string]$env:COMPUTERNAME))) {
            $s = [string]$a.IPAddressToString
            if ($a.AddressFamily -eq 'InterNetwork' -and $s -notmatch '^(127\.|169\.254\.)' -and $ips -notcontains $s) { $ips += $s }
        }
    } catch { }
    if ($ips.Count -eq 0) {
        try {
            foreach ($na in @(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' -ErrorAction SilentlyContinue)) {
                foreach ($a in @($na.IPAddress)) {
                    if ([string]$a -match '^\d+\.\d+\.\d+\.\d+$' -and [string]$a -notmatch '^(127\.|169\.254\.)' -and $ips -notcontains [string]$a) { $ips += [string]$a }
                }
            }
        } catch { }
    }

    $procs = @()
    try { $procs = @(Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { $_.Id -gt 4 }) } catch { }
    if ($procs.Count -eq 0) { try { $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -gt 4 }) } catch { } }

    $users = @()
    foreach ($p in $procs) {
        if ($p.Name -ieq 'explorer' -and $p.UserName -and $users -notcontains [string]$p.UserName) { $users += [string]$p.UserName }
    }

    $known = @{
        'chrome' = 'Chrome'; 'msedge' = 'Microsoft Edge'; 'firefox' = 'Firefox'; 'opera' = 'Opera'
        'brave' = 'Brave'; 'vivaldi' = 'Vivaldi'; 'chromium' = 'Chromium'; 'thorium' = 'Thorium'
        'librewolf' = 'LibreWolf'; 'waterfox' = 'Waterfox'; 'arc' = 'Arc'; 'browser' = 'Yandex Browser'
    }
    $bm = @{}; $bp = 0
    foreach ($p in $procs) {
        $k = $p.Name.ToLowerInvariant()
        if (-not $known.ContainsKey($k)) { continue }
        $l = [string]$known[$k]
        if ($k -eq 'browser') { try { if ([string]$p.Path -notmatch '(?i)yandex') { $l = 'browser' } } catch { } }
        if (-not $bm.ContainsKey($l)) { $bm[$l] = [pscustomobject]@{ name = $l; processes = 0; memory_mb = 0.0; users = @() } }
        $e = $bm[$l]; $e.processes = [int]$e.processes + 1; $bp++
        try { $e.memory_mb = [double]$e.memory_mb + ([double]$p.WorkingSet64 / 1MB) } catch { }
        try { if ($p.UserName -and (@($e.users) -notcontains [string]$p.UserName)) { $e.users = @(@($e.users) + [string]$p.UserName) } } catch { }
    }
    $br = @()
    foreach ($e in $bm.Values) { $e.memory_mb = [math]::Round([double]$e.memory_mb, 1); $br += $e }
    $br = @($br | Sort-Object -Property processes -Descending)

    $top = @()
    try {
        $top = @($procs | Group-Object Name | ForEach-Object {
            $m = 0.0; $c = 0.0
            foreach ($x in $_.Group) {
                try { $m += [double]$x.WorkingSet64 } catch { }
                try { if ($x.CPU) { $c += [double]$x.CPU } } catch { }
            }
            [pscustomobject]@{ name = [string]$_.Name; count = [int]$_.Count; ws_mb = [math]::Round($m / 1MB, 1); cpu_s = [math]::Round($c, 1) }
        } | Sort-Object -Property ws_mb -Descending | Select-Object -First ([math]::Max(1, $Top)))
    } catch { $top = @() }

    $snap = [pscustomobject]@{
        format = 1
        collected_at = $now.ToUniversalTime().ToString('o')
        hostname = [string]$env:COMPUTERNAME
        os = [pscustomobject]@{
            caption = [string]$os.Caption; version = [string]$os.Version; build = [string]$os.BuildNumber
            arch = [string]$os.OSArchitecture; boot_time = $boot.ToUniversalTime().ToString('o'); uptime_s = $up
        }
        cpu = [pscustomobject]@{
            name = if ($cpu) { [string]$cpu.Name } else { '' }; cores = if ($cpu) { [int]$cpu.NumberOfCores } else { 0 }
            logical = if ($cpu) { [int]$cpu.NumberOfLogicalProcessors } else { 0 }; percent = $cp; queue = $q
        }
        memory = [pscustomobject]@{ total_mb = $tm; used_mb = $um; free_mb = $fm; percent = $mp }
        disks = @($disks)
        network = [pscustomobject]@{ addresses = @($ips) }
        users = @($users)
        console_user = if ($cs) { [string]$cs.UserName } else { '' }
        browsers = @($br)
        processes = [pscustomobject]@{ total = [int]$procs.Count; top = @($top) }
        browser_processes = $bp
    }
} catch {
    $snap = [pscustomobject]@{ format = 1; error = [string]$_.Exception.Message }
}

$json = $snap | ConvertTo-Json -Compress -Depth 8
$sb = New-Object System.Text.StringBuilder ($json.Length + 64)
foreach ($ch in $json.ToCharArray()) {
    if ([int]$ch -gt 127) { [void]$sb.Append(('\u{0:x4}' -f [int]$ch)) } else { [void]$sb.Append($ch) }
}
Write-Output ($B + $sb.ToString() + $E)
