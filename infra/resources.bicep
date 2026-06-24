// CCR Lab — resource-group-scoped resources: monitoring, storage, the agent host
// + router Function Apps (each on its OWN Flex Consumption plan), and RBAC.

param location string
param environmentName string
param tags object = {}
param maxInstanceCount int = 40

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var storageAccountName = take('st${replace(resourceToken, '-', '')}', 24)
var agentsAppName = 'func-agents-${resourceToken}'
var routerAppName = 'func-router-${resourceToken}'
var openaiAccountName = 'oai-${resourceToken}'
var openaiApiVersion = '2024-10-21'

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

module openai 'modules/openai.bicep' = {
  name: 'openai'
  params: {
    location: location
    tags: tags
    accountName: openaiAccountName
  }
}

module agentsApp 'modules/functionApp.bicep' = {
  name: 'agentsApp'
  params: {
    location: location
    tags: tags
    appName: agentsAppName
    planName: 'plan-agents-${resourceToken}'
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
    planName: 'plan-router-${resourceToken}'
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
      // Real-embeddings variant of the `embedding` baseline (managed-identity auth).
      { name: 'AZURE_OPENAI_EMBEDDINGS_ENDPOINT', value: openai.outputs.endpoint }
      { name: 'AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT', value: openai.outputs.deploymentName }
      { name: 'AZURE_OPENAI_API_VERSION', value: openaiApiVersion }
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

// Router's managed identity -> "Cognitive Services OpenAI User" on the AOAI account, so
// the embedding baseline can call AOAI via DefaultAzureCredential (no keys).
module routerOpenaiRbac 'modules/openaiRbac.bicep' = {
  name: 'routerOpenaiRbac'
  params: {
    openaiAccountName: openaiAccountName
    principalId: routerApp.outputs.principalId
  }
}

output DATA_STORAGE_ACCOUNT string = storage.outputs.name
output SERVICE_AGENTS_NAME string = agentsApp.outputs.name
output SERVICE_ROUTER_NAME string = routerApp.outputs.name
output AGENTS_BASE_URL string = 'https://${agentsApp.outputs.defaultHostName}/api'
output ROUTER_BASE_URL string = 'https://${routerApp.outputs.defaultHostName}/api'
output OPENAI_ENDPOINT string = openai.outputs.endpoint
