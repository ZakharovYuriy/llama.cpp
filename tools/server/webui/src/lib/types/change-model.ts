export type ModelStatusValue = 'not_configured' | 'stopped' | 'restarting' | 'ready' | 'error';

export interface ModelConfig {
	modelName: string;
	modelPath: string;
	launchArgs: string;
}

export interface ModelStatus {
	status: ModelStatusValue;
	lastError?: string | null;
	updatedAt?: number | null;
	lastRestartAt?: number | null;
}

export interface ModelState extends ModelStatus {
	config: ModelConfig;
}
