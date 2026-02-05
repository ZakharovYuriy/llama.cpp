<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { BookOpen, RefreshCw, Trash2, Upload } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import { Badge } from '$lib/components/ui/badge';
	import * as Table from '$lib/components/ui/table';
	import { cn } from '$lib/components/ui/utils';
	import { formatFileSize } from '$lib/utils';
	import { toast } from 'svelte-sonner';
	import {
		clearRagFiles,
		deleteRagFiles,
		fetchRagState,
		startRagBuild,
		updateRagState,
		uploadRagFiles,
		type RagFile,
		type RagState
	} from '$lib/services/rag';

	const DEFAULT_DOCS_DIR = '/data/docsForLLM';
	const DEFAULT_EMB_MODEL_PATH = '/data/multilingual-e5-small';
	const DEFAULT_INDEX_DIR = '/data/index';
	const DEFAULT_TOP_K = '5';
	const DEFAULT_CHUNK_SIZE = '1000';
	const DEFAULT_CHUNK_OVERLAP = '200';

	interface Props {
		class?: string;
	}

	let { class: className }: Props = $props();

	let state = $state<RagState | null>(null);
	let loading = $state(true);
	let building = $state(false);
	let uploading = $state(false);
	let dirty = $state(false);
	let pollTimer: ReturnType<typeof setInterval> | null = $state(null);
	let fileInput: HTMLInputElement | undefined = $state();

	let enabled = $state(false);
	let docsDir = $state(DEFAULT_DOCS_DIR);
	let embModelPath = $state(DEFAULT_EMB_MODEL_PATH);
	let indexDir = $state(DEFAULT_INDEX_DIR);
	let topK = $state(DEFAULT_TOP_K);
	let chunkSize = $state(DEFAULT_CHUNK_SIZE);
	let chunkOverlap = $state(DEFAULT_CHUNK_OVERLAP);

	function applyState(next: RagState) {
		state = next;
		enabled = next.enabled;
		docsDir = next.config?.docs_dir || DEFAULT_DOCS_DIR;
		embModelPath = next.config?.emb_model_path || DEFAULT_EMB_MODEL_PATH;
		indexDir = next.config?.index_dir || DEFAULT_INDEX_DIR;
		topK = String(next.config?.top_k ?? DEFAULT_TOP_K);
		chunkSize = String(next.config?.chunk_size ?? DEFAULT_CHUNK_SIZE);
		chunkOverlap = String(next.config?.chunk_overlap ?? DEFAULT_CHUNK_OVERLAP);
		building = next.status === 'building';
		dirty = false;
	}

	async function loadState() {
		loading = true;
		try {
			const next = await fetchRagState();
			applyState(next);
		} catch (error) {
			console.error('Failed to load RAG state', error);
			toast.error('Failed to load RAG state');
		} finally {
			loading = false;
		}
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	function startPolling() {
		if (pollTimer) return;
		pollTimer = setInterval(loadState, 2000);
	}

	$effect(() => {
		if (state?.status === 'building') {
			startPolling();
		} else {
			stopPolling();
		}
	});

	onMount(loadState);
	onDestroy(stopPolling);

	async function handleSave(showToast = true) {
		if (!dirty) return true;
		try {
			const docsValue = docsDir.trim() || DEFAULT_DOCS_DIR;
			const embValue = embModelPath.trim() || DEFAULT_EMB_MODEL_PATH;
			const indexValue = indexDir.trim() || DEFAULT_INDEX_DIR;
			const topValue = topK.trim() || DEFAULT_TOP_K;
			const chunkValue = chunkSize.trim() || DEFAULT_CHUNK_SIZE;
			const overlapValue = chunkOverlap.trim() || DEFAULT_CHUNK_OVERLAP;
			const payload = {
				enabled,
				config: {
					docs_dir: docsValue,
					emb_model_path: embValue,
					index_dir: indexValue,
					top_k: Number(topValue),
					chunk_size: Number(chunkValue),
					chunk_overlap: Number(overlapValue)
				}
			};
			if (Number.isNaN(payload.config.top_k)) {
				throw new Error('Top K must be a number');
			}
			if (Number.isNaN(payload.config.chunk_size)) {
				throw new Error('Chunk size must be a number');
			}
			if (Number.isNaN(payload.config.chunk_overlap)) {
				throw new Error('Chunk overlap must be a number');
			}
			const next = await updateRagState(payload);
			applyState(next);
			if (enabled && !next.enabled) {
				toast.warning(next.last_error || 'RAG cannot be enabled yet');
			}
			if (showToast) {
				toast.success('RAG settings saved');
			}
			return true;
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Failed to save RAG settings';
			toast.error(message);
			return false;
		} finally {
		}
	}

	export async function save(showToast = true) {
		return handleSave(showToast);
	}

	export function resetToDefaults() {
		enabled = false;
		docsDir = DEFAULT_DOCS_DIR;
		embModelPath = DEFAULT_EMB_MODEL_PATH;
		indexDir = DEFAULT_INDEX_DIR;
		topK = DEFAULT_TOP_K;
		chunkSize = DEFAULT_CHUNK_SIZE;
		chunkOverlap = DEFAULT_CHUNK_OVERLAP;
		dirty = true;
	}

	function handleChooseFiles() {
		fileInput?.click();
	}

	async function handleFileInput(event: Event) {
		const input = event.target as HTMLInputElement;
		if (!input.files || input.files.length === 0) return;
		const files = Array.from(input.files);
		input.value = '';
		uploading = true;
		try {
			const next = await uploadRagFiles(files);
			applyState(next);
			toast.success(`Uploaded ${files.length} file${files.length === 1 ? '' : 's'}`);
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Upload failed';
			toast.error(message);
		} finally {
			uploading = false;
		}
	}

	async function handleDeleteFile(file: RagFile) {
		try {
			const next = await deleteRagFiles([file.id]);
			applyState(next);
			toast.success(`Deleted ${file.name}`);
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Delete failed';
			toast.error(message);
		}
	}

	async function handleClearAll() {
		try {
			const next = await clearRagFiles();
			applyState(next);
			toast.success('All documents cleared');
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Failed to clear documents';
			toast.error(message);
		}
	}

	async function handleBuild() {
		try {
			const next = await startRagBuild();
			applyState(next);
			toast.info('RAG index build started');
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Failed to start build';
			toast.error(message);
		}
	}

	function ensureDefaultString(value: string, fallback: string, setter: (val: string) => void) {
		if (!value.trim()) {
			setter(fallback);
		}
	}

	$effect(() => {
		if (!state) {
			dirty = false;
			return;
		}
		const config = state.config;
		dirty =
			enabled !== state.enabled ||
			docsDir !== (config?.docs_dir || DEFAULT_DOCS_DIR) ||
			embModelPath !== (config?.emb_model_path || DEFAULT_EMB_MODEL_PATH) ||
			indexDir !== (config?.index_dir || DEFAULT_INDEX_DIR) ||
			topK !== String(config?.top_k ?? DEFAULT_TOP_K) ||
			chunkSize !== String(config?.chunk_size ?? DEFAULT_CHUNK_SIZE) ||
			chunkOverlap !== String(config?.chunk_overlap ?? DEFAULT_CHUNK_OVERLAP);
	});

	function statusVariant(status: RagState['status']) {
		switch (status) {
			case 'ready':
				return 'default';
			case 'building':
				return 'secondary';
			case 'error':
				return 'destructive';
			default:
				return 'outline';
		}
	}

	function statusLabel(status: RagState['status']) {
		switch (status) {
			case 'ready':
				return 'Ready';
			case 'building':
				return 'Building';
			case 'not_built':
				return 'Not built';
			case 'error':
				return 'Error';
			case 'disabled':
				return 'Disabled';
			default:
				return status;
		}
	}
</script>

<div class={cn('space-y-8', className)}>
	<div class="rounded-lg border border-border/40 bg-background p-4">
		<div class="mb-4 flex items-center justify-between gap-4">
			<div class="flex items-center gap-2">
				<BookOpen class="h-4 w-4 text-muted-foreground" />
				<h4 class="text-sm font-medium">RAG Status</h4>
			</div>
			{#if state}
				<Badge variant={statusVariant(state.status)}>{statusLabel(state.status)}</Badge>
			{/if}
		</div>

		<div class="flex flex-wrap items-center justify-between gap-4">
			<div class="flex items-center gap-2">
				<Switch id="rag-enabled" bind:checked={enabled} class="scale-90" />
				<Label for="rag-enabled" class="cursor-pointer text-sm">RAG Enabled</Label>
			</div>

			{#if state}
				<div class="text-xs text-muted-foreground">
					{#if state.stats}
						<span>{state.stats.chunks ?? 0} chunks</span>
					{/if}
				</div>
			{/if}
		</div>

		{#if state?.last_error}
			<p class="mt-3 text-xs text-destructive">{state.last_error}</p>
		{/if}
	</div>

	<div class="space-y-4 rounded-lg border border-border/40 bg-background p-4">
		<div class="flex items-center justify-between">
			<div>
				<h4 class="text-sm font-medium">Documents</h4>
				<p class="text-xs text-muted-foreground">Upload .txt or .md files for retrieval.</p>
			</div>
			<div class="flex items-center gap-2">
				<Button variant="outline" size="sm" onclick={handleChooseFiles} disabled={uploading}>
					<Upload class="h-4 w-4" />
					Add files
				</Button>
				<Button
					variant="ghost"
					size="sm"
					onclick={handleClearAll}
					disabled={!state || state.files.length === 0}
				>
					<Trash2 class="h-4 w-4" />
					Clear all
				</Button>
				<input
					bind:this={fileInput}
					type="file"
					accept=".txt,.md"
					multiple
					class="hidden"
					onchange={handleFileInput}
				/>
			</div>
		</div>

		{#if state && state.files.length > 0}
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Name</Table.Head>
						<Table.Head class="w-[120px]">Size</Table.Head>
						<Table.Head class="w-[140px]">Updated</Table.Head>
						<Table.Head class="w-[80px]"></Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each state.files as file (file.id)}
						<Table.Row>
							<Table.Cell class="font-mono text-xs">{file.name}</Table.Cell>
							<Table.Cell class="text-xs text-muted-foreground">
								{formatFileSize(file.size)}
							</Table.Cell>
							<Table.Cell class="text-xs text-muted-foreground">
								{new Date(file.updated_at * 1000).toLocaleString()}
							</Table.Cell>
							<Table.Cell>
								<Button
									variant="ghost"
									size="sm"
									onclick={() => handleDeleteFile(file)}
								>
									Delete
								</Button>
							</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		{:else if !loading}
			<div class="rounded-md border border-dashed border-border/50 p-4 text-xs text-muted-foreground">
				No documents uploaded yet.
			</div>
		{/if}
	</div>

	<div class="space-y-4 rounded-lg border border-border/40 bg-background p-4">
		<div>
			<h4 class="text-sm font-medium">RAG Parameters</h4>
			<p class="text-xs text-muted-foreground">Configuration persisted on the backend.</p>
		</div>

		<div class="grid gap-4 md:grid-cols-2">
			<div class="space-y-2">
				<Label for="docs-dir">Docs directory</Label>
				<Input
					id="docs-dir"
					bind:value={docsDir}
					placeholder="/data/docsForLLM"
					onblur={() => ensureDefaultString(docsDir, DEFAULT_DOCS_DIR, (v) => (docsDir = v))}
				/>
			</div>
			<div class="space-y-2">
				<Label for="emb-model-path">Embeddings model path</Label>
				<Input
					id="emb-model-path"
					bind:value={embModelPath}
					placeholder="/data/multilingual-e5-small"
					onblur={() =>
						ensureDefaultString(embModelPath, DEFAULT_EMB_MODEL_PATH, (v) => (embModelPath = v))}
				/>
			</div>
			<div class="space-y-2 md:col-span-2">
				<Label for="index-dir">Index directory</Label>
				<Input
					id="index-dir"
					bind:value={indexDir}
					placeholder="/data/index"
					onblur={() => ensureDefaultString(indexDir, DEFAULT_INDEX_DIR, (v) => (indexDir = v))}
				/>
			</div>
			<div class="space-y-2">
				<Label for="top-k">Top K</Label>
				<Input
					id="top-k"
					bind:value={topK}
					onblur={() => ensureDefaultString(topK, DEFAULT_TOP_K, (v) => (topK = v))}
				/>
			</div>
			<div class="space-y-2">
				<Label for="chunk-size">Chunk size</Label>
				<Input
					id="chunk-size"
					bind:value={chunkSize}
					onblur={() => ensureDefaultString(chunkSize, DEFAULT_CHUNK_SIZE, (v) => (chunkSize = v))}
				/>
			</div>
			<div class="space-y-2">
				<Label for="chunk-overlap">Chunk overlap</Label>
				<Input
					id="chunk-overlap"
					bind:value={chunkOverlap}
					onblur={() =>
						ensureDefaultString(chunkOverlap, DEFAULT_CHUNK_OVERLAP, (v) => (chunkOverlap = v))}
				/>
			</div>
		</div>
	</div>

	<div class="space-y-4 rounded-lg border border-border/40 bg-background p-4">
		<div>
			<h4 class="text-sm font-medium">Index</h4>
			<p class="text-xs text-muted-foreground">
				Build the vector index from your uploaded documents. RAG is used only when enabled and
				the index is ready.
			</p>
		</div>

		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" onclick={handleBuild} disabled={building}>
				<RefreshCw class="h-4 w-4" />
				Create index
			</Button>
			{#if state}
				<span class="text-xs text-muted-foreground">
					Status: {statusLabel(state.status)}
				</span>
			{/if}
		</div>
	</div>
</div>
