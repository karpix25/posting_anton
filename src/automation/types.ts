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
  platform: 'instagram' | 'tiktok' | 'youtube';
  theme_key: string;
  last_posted?: Record<string, string>;
}

export interface ScheduledPost {
  video: VideoFile;
  profile: SocialProfile;
  platform: 'instagram' | 'tiktok' | 'youtube';
  publish_at: string;
  caption?: string;
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
