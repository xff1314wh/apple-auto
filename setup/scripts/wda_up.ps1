# 常驻拉起 WebDriverAgent：端口转发 + 运行 XCTest runner
$root = "C:\Users\熊非凡\Doubao\chats\2026-11-24\new-chat\ios-automation"
$ios  = Join-Path $root "bin\ios.exe"
$udid = "00008030-001E44DE02D0802E"
$bid  = "com.facebook.WebDriverAgentRunner.xctrunner.G8Y52ZZLMM"
$tb   = "com.facebook.WebDriverAgentRunner"
$log  = Join-Path $root "out\wda.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "out") | Out-Null

Get-Process ios -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# 1) 本地 8100 -> 设备 8100
$fwd = Start-Process -FilePath $ios -ArgumentList "forward","8100","8100","--udid=$udid" -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2

# 2) 运行 WDA runner（常驻）
$wda = Start-Process -FilePath $ios -ArgumentList "runwda","--bundleid=$bid","--testrunnerbundleid=$tb","--xctestconfig=WebDriverAgentRunner.xctest","--log-output=$log","--udid=$udid" -WindowStyle Hidden -PassThru

# 3) 等 /status 就绪
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" --noproxy "*" --max-time 3 "http://127.0.0.1:8100/status" 2>$null
    if ($code -eq "200") { $ok = $true; break }
    Start-Sleep -Seconds 2
}
if ($ok) {
    "WDA_READY forwarder=$($fwd.Id) runner=$($wda.Id)"
} else {
    "WDA_TIMEOUT forwarder=$($fwd.Id) runner=$($wda.Id) log=$log"
    Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 8
}
