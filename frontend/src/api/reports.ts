import http from './http'
import type { IReport, ICitation } from '@/typing/report'

export function getReports(reportId: string): Promise<IReport> {
    return http.get<IReport>(`/reports/${reportId}`)
}

export function getReportsCitations(reportId: string): Promise<ICitation[]> {
    return http.get<ICitation[]>(`/reports/${reportId}/citations`)
}

export function exportReport(reportId: string): Promise<Blob> {
    return http.get<Blob>(`/reports/${reportId}/export`, { responseType: 'blob' })
}
