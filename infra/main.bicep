// CCR Lab — top-level (subscription-scoped) deployment.
// CREATES the resource group and delegates resource creation to resources.bicep.
// Designed for ephemeral use: deploy with `az deployment sub create`, then tear
// the whole thing down with `az group delete --name <resourceGroupName>`.
//
// Requires subscription-level permission to create resource groups + deployments
// (you have this on a subscription where you can create RGs). If you only have
// Contributor on a pre-existing RG, deploy infra/resources.bicep at group scope
// instead (see DEPLOY.md).

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Short name used to derive unique resource names.')
param environmentName string = 'ccrlab'

@minLength(1)
@description('Primary location for all resources.')
param location string

@description('Name of the resource group to create and deploy into.')
param resourceGroupName string = 'a2a-ccr-lab'

@description('Max scale-out instances per Function App (Flex Consumption).')
param maxInstanceCount int = 40

var tags = {
  project: 'a2a-ccr-lab'
  environment: environmentName
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
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
output AGENTS_BASE_URL string = resources.outputs.AGENTS_BASE_URL
output ROUTER_BASE_URL string = resources.outputs.ROUTER_BASE_URL
output DATA_STORAGE_ACCOUNT string = resources.outputs.DATA_STORAGE_ACCOUNT
output SERVICE_AGENTS_NAME string = resources.outputs.SERVICE_AGENTS_NAME
output SERVICE_ROUTER_NAME string = resources.outputs.SERVICE_ROUTER_NAME
