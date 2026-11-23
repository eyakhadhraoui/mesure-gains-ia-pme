import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe, PercentPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, Metrics, Task } from './api.service';

const FIELDS = ['document_type', 'supplier', 'document_number', 'date', 'total_ht', 'tva', 'total_ttc'];

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, DecimalPipe, PercentPipe],
  template: `
  <header>
    <h1>Agents IA</h1>
    <nav>
      <button [class.primary]="tab() === 'queue'" (click)="tab.set('queue')">File de validation ({{ tasks().length }})</button>
      <button [class.primary]="tab() === 'kpi'" (click)="tab.set('kpi'); loadMetrics()">Indicateurs</button>
    </nav>
  </header>

  <main *ngIf="tab() === 'queue'">
    <p class="muted" *ngIf="!tasks().length">Aucune tâche à valider.</p>
    <section class="card" *ngFor="let t of tasks()" [class.open]="openId === t.id">
      <div class="row" (click)="open(t)">
        <span class="badge">{{ t.kind }}</span>
        <span class="badge cat">{{ t.category || '—' }}</span>
        <span *ngIf="t.agent_output?.urgency === 'haute'" class="badge urgent">urgent</span>
        <strong>{{ t.kind === 'email' ? t.agent_output?.email?.subject : t.agent_output?.filename }}</strong>
        <span class="muted right">{{ t.created_at | date:'dd/MM HH:mm' }}</span>
      </div>

      <div *ngIf="openId === t.id" class="detail">
        <ng-container *ngIf="t.kind === 'email'">
          <p class="muted">De : {{ t.agent_output.email.sender }} — {{ t.agent_output.summary }}</p>
          <pre class="original">{{ t.agent_output.email.body }}</pre>
          <label>Brouillon proposé par l'agent</label>
          <textarea rows="9" [(ngModel)]="draft"></textarea>
          <p class="muted" *ngIf="t.agent_output.sources?.length">Sources : {{ t.agent_output.sources.join(', ') }}</p>
        </ng-container>

        <ng-container *ngIf="t.kind === 'document'">
          <p class="warn" *ngFor="let e of t.agent_output.errors">⚠ {{ e }}</p>
          <div class="grid">
            <ng-container *ngFor="let f of fields">
              <label>{{ f }}</label><input [(ngModel)]="docFields[f]">
            </ng-container>
          </div>
        </ng-container>

        <div class="actions">
          <button class="primary" (click)="submit(t, 'auto')">Valider</button>
          <button (click)="submit(t, 'rejete')">Rejeter</button>
          <span class="muted">Temps de relecture mesuré automatiquement</span>
        </div>
      </div>
    </section>
  </main>

  <main *ngIf="tab() === 'kpi' && metrics() as m">
    <div class="kpis">
      <div class="kpi"><span>Tâches traitées</span><b>{{ m.tasks_total }}</b></div>
      <div class="kpi"><span>Taux d'automatisation</span><b>{{ m.automation_rate ?? 0 | percent:'1.0-0' }}</b></div>
      <div class="kpi"><span>Taux d'erreur</span><b>{{ m.error_rate ?? 0 | percent:'1.0-0' }}</b></div>
      <div class="kpi"><span>Temps gagné</span><b>{{ m.time_saved_hours | number:'1.1-1' }} h</b></div>
      <div class="kpi"><span>Coût IA</span><b>{{ m.llm_cost_eur | number:'1.2-2' }} €</b></div>
      <div class="kpi"><span>Gain net</span><b>{{ m.net_gain_eur | number:'1.0-0' }} €</b></div>
    </div>
    <table>
      <tr><th>Statut</th><th>Nombre</th></tr>
      <tr *ngFor="let s of m.by_status | keyvalue"><td>{{ s.key }}</td><td>{{ s.value }}</td></tr>
    </table>
  </main>
  `,
  styles: [`
    header { display:flex; align-items:center; justify-content:space-between; padding:12px 24px; background:var(--card); border-bottom:1px solid var(--line); }
    h1 { font-size:18px; margin:0; } nav { display:flex; gap:8px; }
    main { max-width: 960px; margin: 24px auto; padding: 0 16px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:8px; margin-bottom:12px; }
    .row { display:flex; gap:8px; align-items:center; padding:12px 16px; cursor:pointer; }
    .right { margin-left:auto; } .muted { color:var(--muted); font-size:13px; }
    .badge { font-size:12px; padding:2px 8px; border-radius:10px; background:var(--bg); border:1px solid var(--line); }
    .badge.cat { color:var(--accent); } .badge.urgent { color:#fff; background:var(--bad); border-color:var(--bad); }
    .detail { padding: 0 16px 16px; display:flex; flex-direction:column; gap:8px; }
    .original { white-space:pre-wrap; background:var(--bg); padding:12px; border-radius:6px; margin:0; font-family:inherit; }
    .grid { display:grid; grid-template-columns: 160px 1fr; gap:6px 12px; align-items:center; }
    .actions { display:flex; gap:8px; align-items:center; } .warn { color:var(--warn); margin:0; }
    .kpis { display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap:12px; margin-bottom:24px; }
    .kpi { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; display:flex; flex-direction:column; gap:6px; }
    .kpi span { color:var(--muted); font-size:13px; } .kpi b { font-size:24px; }
    table { width:100%; border-collapse:collapse; background:var(--card); } td, th { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); }
  `],
})
export class AppComponent implements OnInit {
  private api = inject(ApiService);
  tab = signal<'queue' | 'kpi'>('queue');
  tasks = signal<Task[]>([]);
  metrics = signal<Metrics | null>(null);
  fields = FIELDS;

  openId: number | null = null;
  openedAt = 0;
  draft = '';
  docFields: Record<string, any> = {};

  ngOnInit() { this.load(); }

  load() { this.api.pending().subscribe(t => this.tasks.set(t)); }
  loadMetrics() { this.api.metrics().subscribe(m => this.metrics.set(m)); }

  open(t: Task) {
    if (this.openId === t.id) return;
    this.openId = t.id;
    this.openedAt = performance.now();  // début du chronométrage de la relecture
    this.draft = t.agent_output.draft ?? '';
    this.docFields = { ...(t.agent_output.fields ?? {}) };
  }

  submit(t: Task, choice: 'auto' | 'rejete') {
    const seconds = Math.round((performance.now() - this.openedAt) / 1000);
    let final: any, decision: 'valide' | 'corrige' | 'rejete';
    if (t.kind === 'email') {
      final = { ...t.agent_output, draft: this.draft };
      decision = this.draft.trim() === (t.agent_output.draft ?? '').trim() ? 'valide' : 'corrige';
    } else {
      final = { ...t.agent_output, fields: this.docFields };
      const changed = FIELDS.some(f => String(this.docFields[f] ?? '') !== String(t.agent_output.fields?.[f] ?? ''));
      decision = changed ? 'corrige' : 'valide';
    }
    if (choice === 'rejete') decision = 'rejete';
    this.api.review(t.id, decision, final, seconds).subscribe(() => {
      this.openId = null;
      this.tasks.update(list => list.filter(x => x.id !== t.id));
    });
  }
}
