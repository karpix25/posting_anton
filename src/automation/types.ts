export interface VideoFile {
  name: string;
  path: string;
  url: string;
  md5?: string;
  size?: number;
  created?: string;
}

export interface SocialProfile {
  username: string;
  theme_key: string;
  platforms: ('instagram' | 'tiktok' | 'youtube')[];
  enabled?: boolean; // For soft delete - default true
  limit?: number; // Override global limit
  last_posted?: Record<string, string>;
}

export interface ScheduledPost {
  video: VideoFile;
  profile: SocialProfile;
  platform: 'instagram' | 'tiktok' | 'youtube';
  publish_at: string;
  caption?: string;
  title?: string;
  hashtags?: string[];
}

export interface ClientConfig {
  name: string;
  regex: string;
  prompt: string;
}

export interface AutomationConfig {
  cronSchedule: string;
  yandexToken?: string; // Loaded from env, not config.json usually
  yandexFolders: string[];
  daysToGenerate?: number; // Optional in JSON, default in code
  themeAliases?: Record<string, string[]>; // Canonical -> aliases map
  limits: {
    instagram: number;
    tiktok: number;
    youtube: number;
  };
  profiles: SocialProfile[];
  clients: ClientConfig[];
}
