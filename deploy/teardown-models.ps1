param(
    [Parameter(Mandatory = $true)][string]$SubscriptionId,
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [Parameter(Mandatory = $true)][string]$WorkspaceName
)

$ErrorActionPreference = "Stop"
az account set --subscription $SubscriptionId

az ml online-deployment delete `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --endpoint-name magenticbrain-14b `
  --name blue `
  --yes

az ml online-deployment delete `
  --resource-group $ResourceGroup `
  --workspace-name $WorkspaceName `
  --endpoint-name fara15-9b `
  --name blue `
  --yes

Write-Host "Deployments removed. Endpoints can be deleted separately if desired."

