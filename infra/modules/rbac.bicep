// Grants a Function App's managed identity data-plane access to the storage
// account: Blob Data Owner (deployment container + data/results) and Queue Data
// Contributor (Flex Consumption runtime).

param storageAccountName string
param principalId string
@description('Suffix to keep role-assignment GUIDs unique per app.')
param roleNameSeed string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

// Storage Blob Data Owner
var blobDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
// Storage Queue Data Contributor
var queueDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, blobDataOwner, roleNameSeed)
  scope: storage
  properties: {
    roleDefinitionId: blobDataOwner
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, queueDataContributor, roleNameSeed)
  scope: storage
  properties: {
    roleDefinitionId: queueDataContributor
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
