// Simulate the BROWSER environment exactly: location.origin present, `process` absent.
globalThis.location = { origin: 'http://localhost:5173' }

const { default: api, configureApi } = await import('../src/lib/api.js')

// What does apiUrl('') - the browser base - actually return?
const { API_BASE_URL, apiUrl } = await import('../src/lib/constants.js')
console.log('API_BASE_URL      :', JSON.stringify(API_BASE_URL))
console.log("apiUrl('')        :", JSON.stringify(apiUrl('')))
console.log('  -> trailing slash?', apiUrl('').endsWith('/'))
console.log('')

// Now make a real request through the normal browser code path (no configureApi).
for (const [label, call] of [
  ['GET  /overview', () => api.get('/overview')],
  ['POST /transactions/demo', () => api.post('/transactions/demo', {})],
]) {
  try {
    await call()
    console.log(`${label}: 200 OK`)
  } catch (e) {
    console.log(`${label}: status=${e.status}`)
    console.log(`   ${e.message}`)
  }
}