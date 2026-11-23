import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

export const API_URL = 'http://localhost:8000';
export const API_KEY = localStorage.getItem('apiKey') ?? '';

export interface Task {
  id: number;
  kind: 'email' | 'document';
  category: string;
  status: string;
  agent_output: any;
  created_at: string;
}

export interface Metrics {
  tasks_total: number;
  by_status: Record<string, number>;
  automation_rate: number | null;
  error_rate: number | null;
  time_saved_hours: number;
  llm_cost_eur: number;
  cost_per_task_eur: number | null;
  net_gain_eur: number;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private headers = new HttpHeaders(API_KEY ? { 'X-API-Key': API_KEY } : {});

  pending() {
    return this.http.get<Task[]>(`${API_URL}/tasks?status=a_valider`, { headers: this.headers });
  }

  review(id: number, decision: 'valide' | 'corrige' | 'rejete', finalOutput: any, reviewSeconds: number) {
    return this.http.post(`${API_URL}/tasks/${id}/review`,
      { decision, final_output: finalOutput, review_seconds: reviewSeconds }, { headers: this.headers });
  }

  metrics() {
    return this.http.get<Metrics>(`${API_URL}/metrics`, { headers: this.headers });
  }
}
