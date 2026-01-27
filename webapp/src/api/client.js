/**
 * API Client for Yadro SMM
 *
 * All requests include Telegram init data for authentication.
 */

const API_BASE = 'http://3.121.215.231/api'

async function request(endpoint, options = {}, initData = '') {
  const url = `${API_BASE}${endpoint}`

  const headers = {
    'Content-Type': 'application/json',
    ...(initData && { 'X-Telegram-Init-Data': initData }),
    ...options.headers,
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Request failed' }))
    throw new Error(error.error || error.detail || 'Request failed')
  }

  return response.json()
}

export const api = {
  // Posts
  async getPosts(params = {}, initData) {
    const query = new URLSearchParams(params).toString()
    return request(`/posts${query ? `?${query}` : ''}`, {}, initData)
  },

  async getPost(id, initData) {
    return request(`/posts/${id}`, {}, initData)
  },

  async createPost(data, initData) {
    return request('/posts', {
      method: 'POST',
      body: JSON.stringify(data),
    }, initData)
  },

  async updatePost(id, data, initData) {
    return request(`/posts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }, initData)
  },

  async deletePost(id, initData) {
    return request(`/posts/${id}`, {
      method: 'DELETE',
    }, initData)
  },

  async publishPost(id, initData) {
    return request(`/posts/${id}/publish`, {
      method: 'POST',
    }, initData)
  },

  // AI
  async generatePost(topic, options = {}, initData) {
    return request('/posts/generate', {
      method: 'POST',
      body: JSON.stringify({ topic, ...options }),
    }, initData)
  },

  async editPost(text, instruction, initData) {
    return request('/posts/edit', {
      method: 'POST',
      body: JSON.stringify({ text, instruction }),
    }, initData)
  },

  // Calendar
  async getCalendar(params = {}, initData) {
    const query = new URLSearchParams(params).toString()
    return request(`/calendar${query ? `?${query}` : ''}`, {}, initData)
  },

  async getWeek(offset = 0, initData) {
    return request(`/calendar/week?offset=${offset}`, {}, initData)
  },

  async getSlots(date, initData) {
    return request(`/calendar/slots?date=${date}`, {}, initData)
  },
}
