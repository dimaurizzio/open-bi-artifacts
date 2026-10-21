export function shortDate(value: string | number): string {
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return d.toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/** "hace 5 min", "hace 2 h", "hace 3 días": cuánto hace que se refrescó una fuente. */
export function ago(value?: string | number | null): string {
  if (value == null || value === '') return '';
  const t = typeof value === 'number' ? value * 1000 : Date.parse(value);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return 'recién';
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  const d = Math.round(s / 86400);
  return d === 1 ? 'hace 1 día' : `hace ${d} días`;
}

export function toolLabel(name: string, args: Record<string, unknown>): string {
  const a = args as Record<string, string>;
  const labels: Record<string, () => string> = {
    load_skill: () => `Skill: ${a.skill_name}`,
    load_skill_resource: () => `Lee ${a.skill_name}/${a.path ?? a.file_path ?? ''}`,
    execute_sql_readonly: () => 'Consulta SQL',
    get_table_info: () => `Esquema de ${a.tableId}`,
    get_dataset_info: () => `Dataset ${a.datasetId}`,
    list_table_ids: () => `Tablas de ${a.datasetId}`,
    list_dataset_ids: () => 'Lista datasets',
    artifact_create: () => `Crea ${a.kind === 'analysis' ? 'análisis' : 'dashboard'}: ${a.title}`,
    artifact_write: () => `Escribe ${a.path}`,
    artifact_read: () => `Lee ${a.path || 'resumen'}`,
    artifact_test_dataset: () => `Prueba fuente ${a.dataset_id}`,
    artifact_save_results: () => `Guarda resultados ${a.dataset_id}`,
    artifact_runs: () => 'Lee corridas',
    create_dashboard: () => `Crea el dashboard: ${a.title}`,
    write_db: () => (a.db_op === 'batch' ? `Escribe ${(args.writes as unknown[] | undefined)?.length ?? ''} documentos` : `${a.db_op} ${a.collection}/${a.doc_id}`),
    read_db: () => `Lee ${a.collection}${a.doc_id ? `/${a.doc_id}` : ''}`,
    upload_asset: () => `Sube ${a.name}`,
    delete_asset: () => 'Borra un archivo',
    run_dataset: () => `Corre la fuente ${a.dataset_id}`,
    knowledge_get: () => `Conocimiento sobre ${a.target}`,
    knowledge_search: () => `Busca en el conocimiento: ${a.text}`,
    knowledge_propose: () => (a.draft_id ? 'Guarda conocimiento' : 'Borrador de conocimiento'),
    list_dashboards: () => 'Lista dashboards',
  };
  return (labels[name] ?? (() => name))();
}

/** "anthropic/claude-sonnet-5-5" o "claude-sonnet-5-5-20260101" → "sonnet-5-5". */
export function shortModel(model: string): string {
  const name = model.split('/').pop() ?? model;
  return name.replace(/^claude-/, '').replace(/-\d{8}$/, '');
}

/** Qué está haciendo el agente, para el thinking indicator. */
export function toolActivity(name: string, args: Record<string, unknown>): string {
  const a = args as Record<string, string>;
  switch (name) {
    case 'load_skill':
      return `Cargando la skill ${a.skill_name}`;
    case 'load_skill_resource':
      return `Leyendo ${(a.path ?? a.file_path ?? '').split('/').pop()}`;
    case 'execute_sql_readonly':
      return a.dryRun ? 'Estimando el costo de la consulta' : 'Consultando BigQuery';
    case 'get_table_info':
      return `Revisando el esquema de ${a.tableId}`;
    case 'list_table_ids':
      return `Listando las tablas de ${a.datasetId}`;
    case 'get_dataset_info':
    case 'list_dataset_ids':
      return 'Revisando el warehouse';
    case 'artifact_create':
      return a.kind === 'analysis' ? 'Creando el análisis' : 'Creando el dashboard';
    case 'artifact_write':
      return a.path === 'files/index.html' ? 'Escribiendo la página' : `Escribiendo ${a.path}`;
    case 'artifact_test_dataset':
      return `Probando la fuente ${a.dataset_id}`;
    case 'artifact_save_results':
      return `Guardando los resultados de ${a.dataset_id}`;
    case 'artifact_runs':
      return 'Leyendo las corridas del artifact';
    case 'create_dashboard':
      return 'Creando el dashboard';
    case 'write_db':
      return 'Escribiendo el dashboard';
    case 'read_db':
      return a.collection === 'data/users/me' ? 'Leyendo las corridas' : 'Leyendo el dashboard';
    case 'upload_asset':
      return `Subiendo ${a.name ?? 'un archivo'}`;
    case 'run_dataset':
      return `Corriendo la fuente ${a.dataset_id}`;
    default:
      return `Usando ${name}`;
  }
}
