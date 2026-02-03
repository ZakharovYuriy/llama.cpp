<script lang="ts">
	import { FolderOpen, Loader2, Terminal } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Label from '$lib/components/ui/label/label.svelte';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Card from '$lib/components/ui/card';
	import * as Alert from '$lib/components/ui/alert';
	import { ModelsService } from '$lib/services/models';
	import { modelsStore } from '$lib/stores/models.svelte';
	import type { ApiRouterModelsBootstrapRequest } from '$lib/types';

	let args = $state('');
	let modelPath = $state('');
	let modelName = $state('');
	let isSubmitting = $state(false);
	let errorMessage = $state('');
	let fileInput: HTMLInputElement | null = $state(null);

	const canSubmit = $derived(
		!isSubmitting && (modelPath.trim().length > 0 || args.trim().length > 0)
	);

	function handlePickFile() {
		fileInput?.click();
	}

	function handleFileChange(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;

		const filePath = (file as File & { path?: string }).path;
		modelPath = filePath && filePath.length > 0 ? filePath : file.name;
	}

	async function handleStart() {
		if (!canSubmit) return;

		errorMessage = '';
		isSubmitting = true;

		try {
			const payload: ApiRouterModelsBootstrapRequest = {
				args: args.trim() || undefined,
				model_path: modelPath.trim() || undefined,
				name: modelName.trim() || undefined
			};

			const result = await ModelsService.bootstrap(payload);

			await modelsStore.fetch(true);
			await modelsStore.fetchRouterModels();

			if (result.model) {
				await modelsStore.selectModelById(result.model);
			}

			goto(`#/`);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Failed to start model';
		} finally {
			isSubmitting = false;
		}
	}
</script>

<div class="flex h-full w-full items-center justify-center px-6 py-10">
	<Card.Root class="w-full max-w-3xl">
		<Card.Header class="space-y-2">
			<Card.Title class="flex items-center gap-2 text-xl">
				<Terminal class="h-5 w-5 text-muted-foreground" />
				Model startup
			</Card.Title>
			<Card.Description>
				Enter CLI-style launch arguments and the path to your model file.
			</Card.Description>
		</Card.Header>

		<Card.Content class="space-y-6">
			<div class="space-y-2">
				<Label for="model-args">Launch arguments</Label>
				<Textarea
					id="model-args"
					rows={4}
					placeholder="--n-gpu-layers 30 --ctx-size 4096 --batch-size 512"
					bind:value={args}
				/>
				<p class="text-xs text-muted-foreground">
					Format matches llama-server CLI arguments.
				</p>
			</div>

			<div class="space-y-2">
				<Label for="model-path">Model path</Label>
				<div class="flex flex-col gap-2 sm:flex-row">
					<Input
						id="model-path"
						placeholder="/path/to/model.gguf"
						class="flex-1"
						autocomplete="off"
						bind:value={modelPath}
					/>
	<input
		class="hidden"
		type="file"
		accept=".gguf"
		bind:this={fileInput}
		onchange={handleFileChange}
	/>
	<Button type="button" variant="outline" class="gap-2" onclick={handlePickFile}>
		<FolderOpen class="h-4 w-4" />
		Choose file
	</Button>
				</div>
				<p class="text-xs text-muted-foreground">
					If the browser does not provide the full path, paste it manually.
				</p>
			</div>

			<div class="space-y-2">
				<Label for="model-name">Model name (optional)</Label>
				<Input
					id="model-name"
					placeholder="my-model"
					autocomplete="off"
					bind:value={modelName}
				/>
			</div>

			{#if errorMessage}
				<Alert.Root variant="destructive">
					<Alert.Title>Failed to start model</Alert.Title>
					<Alert.Description>{errorMessage}</Alert.Description>
				</Alert.Root>
			{/if}
		</Card.Content>

		<Card.Footer class="flex items-center justify-between">
			<p class="text-xs text-muted-foreground">After startup, the standard chat UI will open.</p>
	<Button class="gap-2" disabled={!canSubmit} onclick={handleStart}>
				{#if isSubmitting}
					<Loader2 class="h-4 w-4 animate-spin" />
					Starting...
				{:else}
					Start
				{/if}
			</Button>
		</Card.Footer>
	</Card.Root>
</div>
