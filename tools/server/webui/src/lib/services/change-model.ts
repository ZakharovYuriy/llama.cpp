import { getAuthHeaders, getJsonHeaders } from '$lib/utils';
import type { ModelConfig, ModelState, ModelStatus } from '$lib/types/change-model';

async function parseError(response: Response): Promise<string> {
	const clone = response.clone();
	try {
		const data = await response.json();
		if (data?.error) return String(data.error);
		if (data?.message) return String(data.message);
	} catch {}
	try {
		const text = await clone.text();
		if (text) return text;
	} catch {}
	return response.statusText || 'Request failed';
}

async function handleJson<T>(response: Response): Promise<T> {
	if (!response.ok) {
		throw new Error(await parseError(response));
	}
	return (await response.json()) as T;
}

export async function fetchModelConfig(): Promise<ModelConfig> {
	const response = await fetch(`/model/config`, {
		headers: getAuthHeaders()
	});
	return handleJson<ModelConfig>(response);
}

export async function fetchModelStatus(): Promise<ModelStatus> {
	const response = await fetch(`/model/status`, {
		headers: getAuthHeaders()
	});
	return handleJson<ModelStatus>(response);
}

export async function applyModelConfig(payload: ModelConfig): Promise<ModelState> {
	const response = await fetch(`/model/config`, {
		method: 'POST',
		headers: getJsonHeaders(),
		body: JSON.stringify(payload)
	});
	return handleJson<ModelState>(response);
}
