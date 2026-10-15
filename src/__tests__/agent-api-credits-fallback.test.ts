import { describe, it, expect } from 'vitest'
import { classifyAgentResult, isApiCreditFailure } from '../agent.js'

// API-credits mode: a credit/billing/auth failure of the API-key SDK path must
// switch back to the subscription worker; other failures keep their handling.
describe('isApiCreditFailure', () => {
  it('matches an exhausted credit balance result', () => {
    const c = classifyAgentResult({ subtype: 'success', is_error: true, result: 'Credit balance is too low to access the Anthropic API.' })
    expect(isApiCreditFailure(c.reason)).toBe(true)
  })
  it('matches a 401/402 api error status and a bad key', () => {
    expect(isApiCreditFailure('is_error=true api_error_status=402')).toBe(true)
    expect(isApiCreditFailure('invalid x-api-key')).toBe(true)
  })
  it('does not match an AUP block, a timeout, or nothing', () => {
    expect(isApiCreditFailure('is_error=true api_error_status=400 resultSnippet=usage policy')).toBe(false)
    expect(isApiCreditFailure('timeout after 20min')).toBe(false)
    expect(isApiCreditFailure(undefined)).toBe(false)
  })
})
