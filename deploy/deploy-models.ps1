param(
    [Parameter(Mandatory = $true)][string]$SubscriptionId,
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [Parameter(Mandatory = $true)][string]$WorkspaceName
)

$ErrorActionPreference = "Stop"

az account set --subscription $SubscriptionId

Write-Host "Creating/ensuring online endpoints..."
az ml online-endpoint create `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --subscription $SubscriptionId `
  --file "$PSScriptRoot\magenticbrain-endpoint.yml"

az ml online-endpoint create `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --subscription $SubscriptionId `
  --file "$PSScriptRoot\fara-endpoint.yml"

Write-Host "Creating managed deployments..."
az ml online-deployment create `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --subscription $SubscriptionId `
  --file "$PSScriptRoot\magenticbrain-deployment.yml" `
  --all-traffic

az ml online-deployment create `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --subscription $SubscriptionId `
  --file "$PSScriptRoot\fara-deployment.yml" `
  --all-traffic

Write-Host "Done."
