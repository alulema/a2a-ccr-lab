// Grants a managed identity "Cognitive Services OpenAI User" on an AOAI account.
// In a module so the role-assignment GUID is computed from a param (start-computable),
// mirroring modules/rbac.bicep.

param openaiAccountName string
param principalId string

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openaiAccountName
}

// Cognitive Services OpenAI User
var openaiUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, openaiUser)
  scope: account
  properties: {
    roleDefinitionId: openaiUser
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
