# Deploy the backend stack, then the dashboard. Secrets come from .env (gitignored), never from samconfig.toml.
$map = @{ ADMIN_KEY = 'AdminKey'; VONAGE_API_KEY = 'VonageApiKey'; VONAGE_API_SECRET = 'VonageApiSecret';
          VONAGE_SIGNATURE_SECRET = 'VonageSignatureSecret'; VONAGE_WA_SANDBOX = 'VonageWaFrom';
          TELEGRAM_BOT_TOKEN = 'TelegramBotToken' }
$overrides = foreach ($line in Get-Content "$PSScriptRoot\.env") {
    if ($line -match '^\s*([A-Z_]+)=([^#\s]+)' -and $map[$Matches[1]]) { "$($map[$Matches[1]])=$($Matches[2])" }
}
sam deploy --template-file "$PSScriptRoot\template.yaml" --parameter-overrides ($overrides -join ' ') --no-fail-on-empty-changeset

# Telegram webhook: secret = sha256("lu-alert:" + token), same derivation as app.tg_secret()
$tok = ($overrides | Where-Object { $_ -like 'TelegramBotToken=*' }) -replace '^TelegramBotToken=', ''
if ($tok) {
    $api = aws cloudformation describe-stacks --stack-name lu-alert --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue | [0]" --output text
    $sha = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes("lu-alert:$tok"))
    $body = @{ url = "$api/telegram"; secret_token = (-join ($sha | ForEach-Object { $_.ToString('x2') })); allowed_updates = @('message') } | ConvertTo-Json
    $r = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$tok/setWebhook" -ContentType 'application/json' -Body $body
    "Telegram webhook: $($r.description)"
}

# Dashboard -> Amplify Hosting (manual zip deploy, no git connection needed)
$app = aws amplify list-apps --query "apps[?name=='lu-alert-dashboard'].appId | [0]" --output text
if ($app -eq 'None') { $app = aws amplify create-app --name lu-alert-dashboard --platform WEB --query app.appId --output text
                       aws amplify create-branch --app-id $app --branch-name main | Out-Null }
$zip = Join-Path $env:TEMP 'lu-dashboard.zip'
Compress-Archive -Path "$PSScriptRoot\dashboard\*" -DestinationPath $zip -Force
$d = aws amplify create-deployment --app-id $app --branch-name main | ConvertFrom-Json
Invoke-WebRequest -Uri $d.zipUploadUrl -Method Put -InFile $zip -ContentType 'application/zip' -UseBasicParsing | Out-Null
aws amplify start-deployment --app-id $app --branch-name main --job-id $d.jobId | Out-Null
"Dashboard: https://main.$app.amplifyapp.com"
