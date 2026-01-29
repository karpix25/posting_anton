
export interface SocialProfile {
    username: string;
    theme_key?: string;
    enabled: boolean;
    platforms: string[];
    instagramLimit?: number;
    tiktokLimit?: number;
    youtubeLimit?: number;
    proxy?: string;
}

export interface GlobalLimits {
    instagram: number;
    tiktok: number;
    youtube: number;
}

export interface AIClientConfig {
    name: string;
    prompt: string;
    regex: string;
    quota?: number;
}

export interface AppConfig {
    cronSchedule: string;
    yandexFolders: string[];
    daysToGenerate: number;
    limits: GlobalLimits;
    themeAliases: Record<string, string[]>;
    brandQuotas: Record<string, Record<string, number>>;
    profiles: SocialProfile[];
    clients: AIClientConfig[];
}

export interface QuotaUpdatePayload {
    category: string;
    brand: string;
    quota: number;
}

export interface ScheduleConfig {
    enabled: boolean;
    dailyRunTime: string;
    timezone: string;
}
