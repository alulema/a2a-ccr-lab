// Azure OpenAI account + a text-embedding deployment, for the real-embeddings variant of
// the `embedding` routing baseline. Managed-identity only (no keys), matching the rest of
// the lab. The router's identity is granted "Cognitive Services OpenAI User" by the caller.

param location string
param tags object
param accountName string

@description('Embedding model to deploy (OpenAI format).')
param embeddingModel string = 'text-embedding-3-small'

@description('Model version for the embedding deployment.')
param embeddingModelVersion string = '1'

@description('Deployment (alias) name the app calls as the AOAI "model".')
param embeddingDeploymentName string = 'text-embedding-3-small'

@description('TPM capacity (thousands of tokens/min) for the embedding deployment.')
param capacity int = 50

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName   // required for AAD token auth
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true             // managed identity only — no account keys
  }
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: embeddingDeploymentName
  sku: { name: 'Standard', capacity: capacity }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel
      version: embeddingModelVersion
    }
  }
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.endpoint
output deploymentName string = embedding.name
