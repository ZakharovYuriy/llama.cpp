<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { RefreshCw } from '@lucide/svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { cn } from '$lib/components/ui/utils';
	import { toast } from 'svelte-sonner';
	import { applyModelConfig, fetchModelConfig, fetchModelStatus } from '$lib/services/change-model';
	import type { ModelConfig, ModelState, ModelStatus, ModelStatusValue } from '$lib/types/change-model';

	interface Props {
		class?: string;
	}

	let { class: className }: Props = $props();

	let config = $state<ModelConfig | null>(null);
	let status = $state<ModelStatus | null>(null);
	let loading = $state(true);
	let submitting = $state(false);
	let pollTimer: ReturnType<typeof setInterval> | null = $state(null);

	let modelName = $state('');
	let modelPath = $state('');
	let launchArgs = $state('');

	const POLL_INTERVAL_MS = 2000;

	function applyConfig(next: ModelConfig) {
		config = next;
		modelName = next.modelName || '';
		modelPath = next.modelPath || '';
		launchArgs = next.launchArgs || '';
	}

	function applyStatus(next: ModelStatus) {
		status = next;
	}

	function applyState(next: ModelState) {
		applyConfig(next.config);
		applyStatus({
			status: next.status,
			lastError: next.lastError ?? null,
			updatedAt: next.updatedAt ?? null,
			lastRestartAt: next.lastRestartAt ?? null
		});
	}

	async function loadConfig() {
		try {
			const next = await fetchModelConfig();
			applyConfig(next);
		} catch (error) {
			console.error('Failed to load model config', error);
			toast.error('Failed to load model config');
		}
	}

	async function loadStatus() {
		try {
			const next = await fetchModelStatus();
			applyStatus(next);
		} catch (error) {
			console.error('Failed to load model status', error);
			toast.error('Failed to load model status');
		}
	}

	async function loadAll() {
		loading = true;
		await Promise.all([loadConfig(), loadStatus()]);
		loading = false;
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	function startPolling() {
		if (pollTimer) return;
		pollTimer = setInterval(loadStatus, POLL_INTERVAL_MS);
	}

	$effect(() => {
		if (status?.status === 'restarting') {
			startPolling();
		} else {
			stopPolling();
		}
	});

	onMount(loadAll);
	onDestroy(stopPolling);

	function statusVariant(value: ModelStatusValue) {
		switch (value) {
			case 'ready':
				return 'default';
			case 'restarting':
				return 'secondary';
			case 'error':
				return 'destructive';
			default:
				return 'outline';
		}
	}

	function statusLabel(value: ModelStatusValue) {
		switch (value) {
			case 'ready':
				return 'Ready';
			case 'restarting':
				return 'Restarting';
			case 'error':
				return 'Error';
			case 'stopped':
				return 'Stopped';
			case 'not_configured':
				return 'Not configured';
			default:
				return value;
		}
	}

	function formatTimestamp(value?: number | null) {
		if (!value) return '';
		return new Date(value * 1000).toLocaleString();
	}

	async function handleApply() {
		const trimmedPath = modelPath.trim();
		if (!trimmedPath) {
			toast.error('Model path is required');
			return;
		}
		submitting = true;
		try {
			const payload: ModelConfig = {
				modelName: modelName.trim(),
				modelPath: trimmedPath,
				launchArgs
			};
			const next = await applyModelConfig(payload);
			applyState(next);
			toast.success('Model restart initiated');
		} catch (error) {
			const message = error instanceof Error ? error.message : 'Failed to apply model config';
			toast.error(message);
		} finally {
			submitting = false;
		}
	}

	let isRestarting = $derived(status?.status === 'restarting');
	let formDisabled = $derived(loading || submitting || isRestarting);
</script>

<div class={cn('space-y-6', className)}>
	<div class="rounded-lg border border-border/40 bg-background p-4">
		<div class="mb-4 flex items-center justify-between gap-4">
			<div class="flex items-center gap-2">
				<RefreshCw class="h-4 w-4 text-muted-foreground" />
				<h4 class="text-sm font-medium">Model Status</h4>
			</div>
			{#if status}
				<Badge variant={statusVariant(status.status)}>{statusLabel(status.status)}</Badge>
			{/if}
		</div>

		<div class="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
			<div class="space-y-1">
				{#if status?.lastRestartAt}
					<div>Last applied: {formatTimestamp(status.lastRestartAt)}</div>
				{/if}
				{#if status?.updatedAt}
					<div>Last update: {formatTimestamp(status.updatedAt)}</div>
				{/if}
				{#if !status && !loading}
					<div>Status unavailable.</div>
				{/if}
			</div>
			<Button variant="ghost" size="sm" onclick={loadStatus} disabled={loading}>
				Refresh status
			</Button>
		</div>

		{#if status?.lastError}
			<p class="mt-3 text-xs text-destructive">{status.lastError}</p>
		{/if}
	</div>

	<div class="space-y-4 rounded-lg border border-border/40 bg-background p-4">
		<div>
			<h4 class="text-sm font-medium">Model Configuration</h4>
			<p class="text-xs text-muted-foreground">Configuration is persisted on the backend.</p>
		</div>

		<div class="grid gap-4">
			<div class="space-y-2">
				<Label for="model-name">Model name</Label>
				<Input
					id="model-name"
					bind:value={modelName}
					placeholder="my-model"
					disabled={formDisabled}
				/>
			</div>
			<div class="space-y-2">
				<Label for="model-path">Model path</Label>
				<Input
					id="model-path"
					bind:value={modelPath}
					placeholder="/path/to/model.gguf"
					disabled={formDisabled}
				/>
			</div>
			<div class="space-y-2">
				<Label for="launch-args">Server launch params</Label>
				<Textarea
					id="launch-args"
					bind:value={launchArgs}
					placeholder="--ctx-size 4096 --threads 8 --n-gpu-layers 33"
					rows={4}
					disabled={formDisabled}
				/>
				<p class="text-xs text-muted-foreground">
					Use llama-server CLI flags. Model path and alias are taken from the fields above unless you include
					<code>--model</code> or <code>--alias</code> here.
				</p>
			</div>
		</div>

		{#if !loading && !modelPath.trim()}
			<p class="text-xs text-destructive">Model path is required.</p>
		{/if}
	</div>

	<div class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/40 bg-background p-4">
		<div class="text-xs text-muted-foreground">
			{#if isRestarting}
				Applying configuration. The backend may be unavailable during restart.
			{:else}
				Apply changes to restart llama-server with the new model.
			{/if}
		</div>
		<Button onclick={handleApply} disabled={formDisabled || !modelPath.trim()}>
			{submitting || isRestarting ? 'Applying...' : 'Apply / Change model'}
		</Button>
	</div>
</div>
