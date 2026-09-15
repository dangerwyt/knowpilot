import http from '@/api/http'

export function health (url: string) {
  return http({
    url,
    method: 'get'
  })
}