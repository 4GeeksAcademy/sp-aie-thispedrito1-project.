"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.talentApi = void 0;
// /services/api.ts
const tracker_1 = require("../types/tracker");
const BASE_URL = globalThis.process?.env?.NEXT_PUBLIC_API_URL ||
    'https://playground.4geeks.com/tracker/api/v1';
const normalizeNote = (raw) => ({
    id: raw?.id,
    candidate_id: raw?.candidate_id || raw?.record_id || '',
    record_id: raw?.record_id || raw?.candidate_id || '',
    content: raw?.content || '',
    created_at: raw?.created_at || '',
});
const parseNotesPayload = (payload) => {
    if (Array.isArray(payload)) {
        return payload.map(normalizeNote);
    }
    if (payload && typeof payload === 'object') {
        const nested = Array.isArray(payload.data)
            ? payload.data
            : Array.isArray(payload.records)
                ? payload.records
                : [];
        return nested.map(normalizeNote);
    }
    return [];
};
async function handleResponse(response) {
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        // TRUCO DIAGNÓSTICO: Si es un error 422, serializamos el objeto "detail" completo 
        // que envía FastAPI para saber exactamente qué campo está fallando.
        if (response.status === 422 && errorData.detail) {
            throw new Error(`API_422_DETAIL: ${JSON.stringify(errorData.detail)}`);
        }
        throw new Error(errorData.detail || `Error HTTP: ${response.status}`);
    }
    return response.json();
}
exports.talentApi = {
    getRecords: async () => {
        const response = await fetch(`${BASE_URL}/records`, { cache: 'no-store' });
        return handleResponse(response);
    },
    getRecordById: async (id) => {
        const response = await fetch(`${BASE_URL}/records/${id}`, { cache: 'no-store' });
        return handleResponse(response);
    },
    createRecord: async (data) => {
        const response = await fetch(`${BASE_URL}/records`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(response);
    },
    updateRecord: async (id, data) => {
        const response = await fetch(`${BASE_URL}/records/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return handleResponse(response);
    },
    patchRecordStatus: async (id, cleanPayload) => {
        const response = await fetch(`${BASE_URL}/records/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cleanPayload),
        });
        return handleResponse(response);
    },
    getNotes: async (candidateId) => {
        const response = await fetch(`${BASE_URL}/records/${candidateId}/notes`, { cache: 'no-store' });
        if (response.status === 404)
            return [];
        const payload = await handleResponse(response);
        return parseNotesPayload(payload);
    },
    addNote: async (candidateId, content) => {
        const response = await fetch(`${BASE_URL}/records/${candidateId}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        const payload = await handleResponse(response);
        const notePayload = payload?.data && typeof payload.data === 'object' ? payload.data : payload;
        return normalizeNote(notePayload);
    },
    deleteNote: async (candidateId, noteId) => {
        const response = await fetch(`${BASE_URL}/records/${candidateId}/notes/${noteId}`, {
            method: 'DELETE',
        });
        if (!response.ok)
            throw new Error(`Error al eliminar la nota: ${response.status}`);
    },
};
//# sourceMappingURL=api.js.map