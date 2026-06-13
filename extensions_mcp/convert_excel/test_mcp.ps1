# Test MCP Server Connection
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

Write-Host "=== Step 1: Initialize ===" -ForegroundColor Cyan
$r = Invoke-WebRequest -Uri "http://127.0.0.1:6000/mcp" -Method POST -ContentType "application/json" -Headers @{"Accept"="application/json, text/event-stream"} -Body $body -UseBasicParsing

Write-Host "Status: $($r.StatusCode)" -ForegroundColor Green
Write-Host "Response:" -ForegroundColor Yellow
Write-Host $r.Content

$sid = $r.Headers['mcp-session-id']
Write-Host "`nSession ID: $sid" -ForegroundColor Magenta

if ($sid) {
    Write-Host "`n=== Step 2: List Tools ===" -ForegroundColor Cyan
    $body2 = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:6000/mcp" -Method POST -ContentType "application/json" -Headers @{"Accept"="application/json, text/event-stream";"mcp-session-id"=$sid} -Body $body2 -UseBasicParsing
    
    Write-Host "Status: $($r2.StatusCode)" -ForegroundColor Green
    Write-Host "Tools List:" -ForegroundColor Yellow
    Write-Host $r2.Content
} else {
    Write-Host "`nERROR: No session ID received!" -ForegroundColor Red
}
