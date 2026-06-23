// CCR Lab — resource-group-scoped resources: monitoring, storage, a shared Flex
// Consumption plan, the agent host + router Function Apps, and RBAC.

param location string
param environmentName string
param tags object = {}
param maxInstanceCount int = 40

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var storageAccountName = take('st${replace(resourceToken, '-', '')}', 24)
var planName = 'plan-${resourceToken}'
var agentsAppName = 'func-agents-${resourceToken}'
var routerAppName = 'func-router-${resourceToken}'

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    logAnalyticsName: 'log-${resourceToken}'
    appInsightsName: 'appi-${resourceToken}'
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    tags: tags
    storageAccountName: storageAccountName
  }
}

// Shared Flex Consumption plan for both apps.
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

module agentsApp 'modules/functionApp.bicep' = {
  name: 'agentsApp'
  params: {
    location: location
    tags: tags
    appName: agentsAppName
    planId: plan.id
    storageAccountName: storage.outputs.name
    blobEndpoint: storage.outputs.blobEndpoint
    deploymentContainerName: 'deploy-agents'
    appInsightsConnectionString: monitoring.outputs.connectionString
    maxInstanceCount: maxInstanceCount
    serviceTag: 'agents'
    appSettings: [
      { name: 'DATA_STORAGE_ACCOUNT', value: storage.outputs.name }
      { name: 'DATA_CONTAINER', value: 'data' }
    ]
  }
}

module routerApp 'modules/functionApp.bicep' = {
  name: 'routerApp'
  params: {
    location: location
    tags: tags
    appName: routerAppName
    planId: plan.id
    storageAccountName: storage.outputs.name
    blobEndpoint: storage.outputs.blobEndpoint
    deploymentContainerName: 'deploy-router'
    appInsightsConnectionString: monitoring.outputs.connectionString
    maxInstanceCount: maxInstanceCount
    serviceTag: 'router'
    appSettings: [
      { name: 'DATA_STORAGE_ACCOUNT', value: storage.outputs.name }
      { name: 'DATA_CONTAINER', value: 'data' }
      { name: 'RESULTS_CONTAINER', value: 'results' }
      { name: 'AGENT_BASE_URL', value: 'https://${agentsApp.outputs.defaultHostName}/api' }
    ]
  }
}

module agentsRbac 'modules/rbac.bicep' = {
  name: 'agentsRbac'
  params: {
    storageAccountName: storage.outputs.name
    principalId: agentsApp.outputs.principalId
    roleNameSeed: 'agents'
  }
}

module routerRbac 'modules/rbac.bicep' = {
  name: 'routerRbac'
  params: {
    storageAccountName: storage.outputs.name
    principalId: routerApp.outputs.principalId
    roleNameSeed: 'router'
  }
}

output DATA_STORAGE_ACCOUNT string = storage.outputs.name
output SERVICE_AGENTS_NAME string = agentsApp.outputs.name
output SERVICE_ROUTER_NAME string = routerApp.outputs.name
output AGENTS_BASE_URL string = 'https://${agentsApp.outputs.defaultHostName}/api'
output ROUTER_BASE_URL string = 'https://${routerApp.outputs.defaultHostName}/api'
