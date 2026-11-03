import type { OutingScheduleState } from './outingClock';

export interface DialogueExample { user: string; assistant: string }
export interface Persona { id: string; name: string; personality: string; examples?: DialogueExample[]; avatar?: string | null }
export interface Topic { id: string; title: string }
export interface Memory { id: string; text: string; sourceMessageId: string | null; sourceExcerpt?: string | null }
export interface Suggestion { id: string; sourceMessageId: string; text: string }
export interface ExperienceInput { title: string; excerpt: string; url: string; impression: string }
export interface Experience extends ExperienceInput {
  id: string; evidence: 'title' | 'excerpt'; source: 'user-share'; scope: 'private'; createdAt: number; updatedAt: number;
}
export type RunStatus = 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed' | 'interrupted';
export interface Message {
  id: string; role: 'user' | 'assistant'; text: string; status: RunStatus; turnId: string; memoryIds: string[]; experienceIds?: string[];
  outingSources?: {id: string; title: string}[];
}
export interface Run { id: string; topicId: string; status: RunStatus; text: string; error?: string | null }
export interface Connection { baseUrl: string; model: string; hasKey: boolean }
export interface ConnectionProfile extends Connection { id: string; name: string }
export interface Connections { activeId: string | null; profiles: ConnectionProfile[] }
export interface Snapshot {
  persona: Persona; topics: Topic[]; currentTopicId: string; messages: Message[]; memories: Memory[];
  connection: Connection; run: Run | null; hasOlderMessages?: boolean;
  experiences?: Experience[]; suggestions?: Suggestion[];
  outingSchedule?: OutingScheduleState;
}
export type Save = (action: 'profile' | 'connection.select' | 'connection.upsert' | 'remember' | 'revise' | 'forget' | 'suggestion'
  | 'experience.edit' | 'experience.remove' | 'outing.forget', values: Record<string, unknown>) => Promise<boolean>;
export type Share = (values: ExperienceInput) => Promise<boolean>;
export const isActive = (run?: Run | null) => !!run && (run.status === 'running' || run.status === 'cancelling');
