// Storage account backing both Function Apps (Flex Consumption deployment
// containers) and holding the experiment data + results.

param location string
param tags object
param storageAccountName string

@description('Blob containers to create.')
param containers array = [
  'deploy-agents'   // Flex Consumption deployment package for the agent host
  'deploy-router'   // Flex Consumption deployment package for the router
  'data'            // tasks.json + agents.json (seeded)
  'results'         // experiment output
]

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false   // managed identity only — no account keys
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for c in containers: {
    parent: blobService
    name: c
    properties: { publicAccess: 'None' }
  }
]

output name string = storage.name
output id string = storage.id
output blobEndpoint string = storage.properties.primaryEndpoints.blob
