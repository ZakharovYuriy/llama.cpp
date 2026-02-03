<script lang="ts">
	import {
		ChatScreen,
		DialogModelNotAvailable,
		ServerBootstrap,
		ServerErrorSplash,
		ServerLoadingSplash
	} from '$lib/components/app';
	import { chatStore } from '$lib/stores/chat.svelte';
	import { conversationsStore, isConversationsInitialized } from '$lib/stores/conversations.svelte';
	import { modelsStore, modelOptions, modelsLoading } from '$lib/stores/models.svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import { isRouterMode, serverError, serverLoading } from '$lib/stores/server.svelte';

	let qParam = $derived(page.url.searchParams.get('q'));
	let modelParam = $derived(page.url.searchParams.get('model'));
	let newChatParam = $derived(page.url.searchParams.get('new_chat'));

	// Dialog state for model not available error
	let showModelNotAvailable = $state(false);
	let requestedModelName = $state('');
	let availableModelNames = $derived(modelOptions().map((m) => m.model));
	let isServerLoading = $derived(serverLoading());
	let serverErrorMessage = $derived(serverError());
	let isRouter = $derived(isRouterMode());
	let isModelsLoading = $derived(modelsLoading());
	let showBootstrap = $derived(isRouter && !isModelsLoading && modelOptions().length === 0);

	/**
	 * Clear URL params after message is sent to prevent re-sending on refresh
	 */
	function clearUrlParams() {
		const url = new URL(page.url);

		url.searchParams.delete('q');
		url.searchParams.delete('model');
		url.searchParams.delete('new_chat');

		replaceState(url.toString(), {});
	}

	async function handleUrlParams() {
		await modelsStore.fetch();

		if (modelParam) {
			const model = modelsStore.findModelByName(modelParam);

			if (model) {
				try {
					await modelsStore.selectModelById(model.id);
				} catch (error) {
					console.error('Failed to select model:', error);
					requestedModelName = modelParam;
					showModelNotAvailable = true;

					return;
				}
			} else {
				requestedModelName = modelParam;
				showModelNotAvailable = true;

				return;
			}
		}

		// Handle ?q= parameter - create new conversation and send message
		if (qParam !== null) {
			await conversationsStore.createConversation();
			await chatStore.sendMessage(qParam);
			clearUrlParams();
		} else if (modelParam || newChatParam === 'true') {
			clearUrlParams();
		}
	}

	onMount(async () => {
		if (!isConversationsInitialized()) {
			await conversationsStore.initialize();
		}

		conversationsStore.clearActiveConversation();
		chatStore.clearUIState();

		await modelsStore.fetch().catch((error) => {
			console.warn('Unable to load models:', error);
		});

		// Handle URL params only if we have ?q= or ?model= or ?new_chat=true
		if (qParam !== null || modelParam !== null || newChatParam === 'true') {
			await handleUrlParams();
		}
	});
</script>

<svelte:head>
	<title>llama.cpp - AI Chat Interface</title>
</svelte:head>

{#if isServerLoading}
	<ServerLoadingSplash />
{:else if serverErrorMessage}
	<ServerErrorSplash error={serverErrorMessage} />
{:else if showBootstrap}
	<ServerBootstrap />
{:else}
	<ChatScreen showCenteredEmpty={true} />
{/if}

<DialogModelNotAvailable
	bind:open={showModelNotAvailable}
	modelName={requestedModelName}
	availableModels={availableModelNames}
/>
