// CCR Lab — top-level (subscription-scoped) deployment.
// Creates a resource group and delegates resource creation to resources.bicep.
// Compatible with `azd up` (azure.yaml) and plain `az deployment sub create`.

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment (used to derive resource names and the resource group).')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
param location string

@description('Max scale-out instances per Function App (Flex Consumption).')
param maxInstanceCount int = 40

var tags = {
  'azd-env-name': environmentName
  project: 'a2a-ccr-lab'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'ccr-resources'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    maxInstanceCount: maxInstanceCount
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AGENTS_BASE_URL string = resources.outputs.agentsBaseUrl
output ROUTER_BASE_URL string = resources.outputs.routerBaseUrl
output DATA_STORAGE_ACCOUNT string = resources.outputs.storageAccountName
output SERVICE_AGENTS_NAME string = resources.outputs.agentsAppName
output SERVICE_ROUTER_NAME string = resources.outputs.routerAppName
