export interface OutingScheduleState {
  enabled: boolean;
  nextAt: number | null;
  attemptsToday: number;
  dailyLimit: number;
  intervalMinutes: number;
}

export interface OutingTick {
  schedule: OutingScheduleState;
  startedId: string | null;
}
