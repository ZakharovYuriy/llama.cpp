import { getAuthHeaders, getJsonHeaders } from '$lib/utils';

export interface RagConfig {
	docs_dir: string;
	emb_model_path: string;
	index_dir: string;
	top_k: number;
	chunk_size: number;
	chunk_overlap: number;
}

export interface RagFile {
	id: string;
	name: string;
	size: number;
	updated_at: number;
}

export interface RagStats {
	chunks: number;
	index_size: number;
}

export interface RagState {
	enabled: boolean;
	status: 'disabled' | 'not_built' | 'building' | 'ready' | 'error';
	last_error?: string | null;
	config: RagConfig;
	files: RagFile[];
	stats: RagStats;
	building: boolean;
}

async function parseError(response: Response): Promise<string> {
	try {
		const data = await response.json();
		if (data?.error) return String(data.error);
		if (data?.message) return String(data.message);
	} catch {}
	return response.statusText || 'Request failed';
}

async function handleJson<T>(response: Response): Promise<T> {
	if (!response.ok) {
		throw new Error(await parseError(response));
	}
	return (await response.json()) as T;
}

export async function fetchRagState(): Promise<RagState> {
	const response = await fetch(`./rag/state`, {
		headers: getAuthHeaders()
	});
	return handleJson<RagState>(response);
}

export async function updateRagState(payload: Partial<{ enabled: boolean; config: Partial<RagConfig> }>) {
	const response = await fetch(`./rag/state`, {
		method: 'POST',
		headers: getJsonHeaders(),
		body: JSON.stringify(payload)
	});
	return handleJson<RagState>(response);
}

export async function uploadRagFiles(files: File[]): Promise<RagState> {
	const form = new FormData();
	for (const file of files) {
		form.append('files', file, file.name);
	}
	const response = await fetch(`./rag/files`, {
		method: 'POST',
		headers: getAuthHeaders(),
		body: form
	});
	return handleJson<RagState>(response);
}

export async function deleteRagFiles(ids: string[]): Promise<RagState> {
	const response = await fetch(`./rag/files/delete`, {
		method: 'POST',
		headers: getJsonHeaders(),
		body: JSON.stringify({ ids })
	});
	return handleJson<RagState>(response);
}

export async function clearRagFiles(): Promise<RagState> {
	const response = await fetch(`./rag/files/clear`, {
		method: 'POST',
		headers: getJsonHeaders()
	});
	return handleJson<RagState>(response);
}

export async function startRagBuild(): Promise<RagState> {
	const response = await fetch(`./rag/build`, {
		method: 'POST',
		headers: getJsonHeaders()
	});
	return handleJson<RagState>(response);
}
