// Reusable Flex Consumption Function App (Linux, Python) with a system-assigned
// managed identity. Used for both the agent host and the router.

param location string
param tags object
param appName string
param planId string
param storageAccountName string
param blobEndpoint string
param deploymentContainerName string
param appInsightsConnectionString string
param maxInstanceCount int = 40
param instanceMemoryMB int = 2048
param runtimeVersion string = '3.11'

@description('azd service name tag so `azd deploy` targets the right app.')
param serviceTag string

@description('Extra app settings specific to this app.')
param appSettings array = []

var baseSettings = [
  {
    name: 'AzureWebJobsStorage__accountName'
    value: storageAccountName
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
]

resource site 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': serviceTag })
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: planId
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${blobEndpoint}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: maxInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
      runtime: {
        name: 'python'
        version: runtimeVersion
      }
    }
    siteConfig: {
      appSettings: concat(baseSettings, appSettings)
    }
  }
}

output name string = site.name
output principalId string = site.identity.principalId
output defaultHostName string = site.properties.defaultHostName
