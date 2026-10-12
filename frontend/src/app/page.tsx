'use client';

import { FormEvent, ReactNode, useState } from 'react';

type CommandKind = 'setup' | 'run' | 'test' | 'build' | 'quality' | 'other';

interface RunCommand {
  kind: CommandKind;
  command: string;
  working_directory: string;
  source_path: string;
  line: number;
  inferred: boolean;
}

interface RepositoryFacts {
  commands: RunCommand[];
  entry_points: { path: string; reason: string }[];
  coverage: {
    readme_found: boolean;
    readme_truncated: boolean;
    tree_entries_inspected: number;
    tree_entries_total: number | null;
    tree_truncated: boolean;
    source_files_inspected: string[];
    source_files_skipped: number;
  };
}

interface Recommendation {
  suggestion: string;
  rationale: string;
  path: string;
  quote: string;
  line_start: number;
  line_end: number;
  verified: boolean;
}

interface RepositoryAnalysis {
  executive_summary: string;
  technologies_used: string[];
  architecture_overview: string;
  main_components: string[];
  folder_responsibilities: string[];
  first_steps: string[];
  reading_order?: { path: string; reason: string; verified: boolean }[];
  recommendations?: Recommendation[];
  unknowns?: string[];
  evidence: { claim: string; path: string; quote: string; line_start: number; line_end: number }[];
}

interface RepositoryDetails {
  name: string;
  owner: string;
  description: string | null;
  stars: number;
  forks: number;
  default_branch: string;
  primary_language: string | null;
  languages: { name: string; bytes: number; percentage: number }[];
}

interface RepositoryAnalysisResult {
  repository: RepositoryDetails;
  facts?: RepositoryFacts | null;
  analysis: RepositoryAnalysis;
}

type SourceUrl = (path: string, lineStart?: number, lineEnd?: number) => string;

// Production builds must set NEXT_PUBLIC_API_BASE_URL to the deployed backend URL.
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? (
  process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8000' : ''
);

const loadingStages = [
  'Fetching the public repository from GitHub',
  'Reading the README, file tree, and key source files',
  'Extracting commands and entry points',
  'Writing the onboarding guide',
];

const examples = ['fastapi/full-stack-fastapi-template', 'pallets/flask', 'insikizi23/repo-lens'];

const commandKindLabels: Record<CommandKind, string> = {
  setup: 'Setup',
  run: 'Run',
  test: 'Test',
  build: 'Build',
  quality: 'Lint',
  other: 'Other',
};

const languageColors = ['#5eb4ff', '#b6a4ff', '#4ade80', '#fbbf24', '#f472b6', '#2dd4bf', '#fb923c'];

const numberFormatter = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

export default function HomePage() {
  const [repositoryUrl, setRepositoryUrl] = useState('');
  const [result, setResult] = useState<RepositoryAnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState(0);
  const [error, setError] = useState('');

  async function analyze(input: string) {
    const normalizedUrl = normalizeRepositoryUrl(input);
    if (!normalizedUrl) {
      setError('Enter a public GitHub repository, for example https://github.com/owner/repository or owner/repository.');
      return;
    }
    if (!apiBaseUrl) {
      setError('RepoLens is not configured: set NEXT_PUBLIC_API_BASE_URL to the backend URL and redeploy.');
      return;
    }

    setRepositoryUrl(normalizedUrl);
    setLoading(true);
    setLoadingStage(0);
    setError('');
    setResult(null);

    const stageTimers = [
      window.setTimeout(() => setLoadingStage(1), 900),
      window.setTimeout(() => setLoadingStage(2), 2200),
      window.setTimeout(() => setLoadingStage(3), 4200),
    ];

    try {
      const response = await fetch(`${apiBaseUrl}/api/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repository_url: normalizedUrl }),
      });

      if (!response.ok) {
        const errorData: unknown = await response.json().catch(() => ({}));
        const detail =
          typeof errorData === 'object' && errorData !== null && 'detail' in errorData && typeof errorData.detail === 'string'
            ? errorData.detail
            : `RepoLens API returned HTTP ${response.status}. Check the API health endpoint: ${apiBaseUrl}/health`;
        throw new Error(detail);
      }

      const data: unknown = await response.json();
      if (!isRepositoryAnalysisResult(data)) {
        throw new Error('The backend returned an unexpected analysis response. Restart the backend and try again.');
      }
      setResult(data);
    } catch (caughtError) {
      setError(
        caughtError instanceof TypeError
          ? `Could not connect to the RepoLens API. It may be waking up (free hosting can take about 30 seconds), so try again shortly. If it keeps failing, the API's CORS_ORIGINS setting may not include ${window.location.origin}.`
          : caughtError instanceof Error
            ? caughtError.message
            : 'Unexpected error while analyzing the repository.'
      );
    } finally {
      stageTimers.forEach((timer) => window.clearTimeout(timer));
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void analyze(repositoryUrl);
  }

  const hasOutput = loading || result !== null;

  return (
    <>
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href="/" className="brand" aria-label="RepoLens home">
            <span className="brand-mark" aria-hidden="true"><LensIcon /></span>
            RepoLens
          </a>
          <span className="pill" title="RepoLens reads repositories through GitHub's public API and cannot access private repositories.">
            <GlobeIcon /> Public repos only
          </span>
        </div>
      </header>

      <main className="shell">
        <section className={hasOutput ? 'hero compact' : 'hero'}>
          {hasOutput ? null : (
            <>
              <h1>
                Get productive in a new codebase <span className="accent">on day one.</span>
              </h1>
              <p className="lede">
                Paste a public GitHub repository. RepoLens finds the real setup, run, and test commands, points you to the
                files worth reading first, and links every claim back to the source.
              </p>
            </>
          )}

          <form onSubmit={handleSubmit} className="search">
            <label className="search-field">
              <GitHubIcon />
              <input
                value={repositoryUrl}
                onChange={(event) => setRepositoryUrl(event.target.value)}
                placeholder="https://github.com/owner/repository"
                aria-label="Public GitHub repository URL"
                disabled={loading}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <button type="submit" className="button" disabled={loading}>
              {loading ? 'Analyzing…' : 'Analyze'}
            </button>
          </form>

          <p className="hint">
            <span>Try:</span>
            {examples.map((example) => (
              <button key={example} type="button" className="chip" disabled={loading} onClick={() => void analyze(example)}>
                {example}
              </button>
            ))}
          </p>

          {hasOutput ? null : (
            <div className="notice">
              <LockIcon />
              <span>
                <strong style={{ color: 'var(--text)' }}>Public repositories only.</strong> RepoLens reads code through
                GitHub&apos;s public API and never asks for access to your account, so private repositories (including private
                organization repos) will show as not found.
              </span>
            </div>
          )}

          {error ? <p role="alert" className="error">{error}</p> : null}
        </section>

        {loading ? <LoadingState stage={loadingStage} /> : null}

        {result ? <Results result={result} /> : null}

        {hasOutput ? null : <Features />}
      </main>

      <footer className="footer">
        <div className="shell">
          RepoLens works with public GitHub repositories only. Sections marked “AI generated” are a starting point: check
          the linked sources before acting on them.
        </div>
      </footer>
    </>
  );
}

function Results({ result }: { result: RepositoryAnalysisResult }) {
  const { repository, facts, analysis } = result;
  const sourceUrl: SourceUrl = (path, lineStart, lineEnd) => {
    const encodedPath = path.split('/').map(encodeURIComponent).join('/');
    const fragment = !lineStart ? '' : lineEnd && lineEnd !== lineStart ? `#L${lineStart}-L${lineEnd}` : `#L${lineStart}`;
    return `https://github.com/${repository.owner}/${repository.name}/blob/${repository.default_branch}/${encodedPath}${fragment}`;
  };

  const tabs = [
    ['overview', 'Overview'],
    ['run', 'Get it running'],
    ['read', 'Start reading'],
    ['steps', 'First steps'],
    ['recommendations', 'Recommendations'],
    ['questions', 'Open questions'],
  ].filter(([id]) => {
    if (id === 'run') return Boolean(facts);
    if (id === 'read') return Boolean(analysis.reading_order?.length);
    if (id === 'recommendations') return Boolean(analysis.recommendations?.length);
    if (id === 'questions') return Boolean(analysis.unknowns?.length);
    return true;
  });

  return (
    <div className="results">
      <RepositoryHeader repository={repository} />

      <nav className="tabs" aria-label="Jump to section">
        {tabs.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
      </nav>

      <div className="results-grid">
        <div className="column">
          <Card id="overview" title="Overview" badge="ai">
            <p className="prose">{analysis.executive_summary}</p>
            <p className="prose">{analysis.architecture_overview}</p>
          </Card>

          {facts ? (
            <Card id="run" title="Get it running" badge="verified" note="Taken directly from the repository's files. Nothing here is written by AI.">
              <CommandList commands={facts.commands} sourceUrl={sourceUrl} />
            </Card>
          ) : null}

          {analysis.reading_order?.length ? (
            <Card id="read" title="Start reading here" badge="ai" note="A suggested order. Every path was checked against the repository's file tree.">
              <ol className="steps">
                {analysis.reading_order.map((step) => (
                  <li key={step.path}>
                    <div>
                      <a href={sourceUrl(step.path)} target="_blank" rel="noreferrer" className="path">{step.path}</a>
                      {step.verified ? null : <span className="tag warn">not in inspected tree</span>}
                      {step.reason ? <div className="sub">{step.reason}</div> : null}
                    </div>
                  </li>
                ))}
              </ol>
            </Card>
          ) : null}

          <Card id="steps" title="Your first steps" badge="ai">
            {analysis.first_steps.length ? (
              <ol className="steps">
                {analysis.first_steps.map((step, index) => <li key={`${step}-${index}`}><div>{step}</div></li>)}
              </ol>
            ) : <p className="card-note">No first steps were generated.</p>}
          </Card>

          {analysis.recommendations?.length ? (
            <Card id="recommendations" title="Recommendations" badge="ai" note="“Backed by source” means the quoted code was matched word-for-word against the file.">
              {analysis.recommendations.map((item, index) => (
                <div key={`${item.suggestion}-${index}`} className="rec">
                  <div>
                    <strong>{item.suggestion}</strong>
                    {item.verified ? <span className="tag ok">backed by source</span> : <span className="tag warn">no verified source</span>}
                  </div>
                  {item.rationale ? <p className="prose">{item.rationale}</p> : null}
                  {item.verified ? (
                    <Quote quote={item.quote} href={sourceUrl(item.path, item.line_start, item.line_end)} label={lineLabel(item.path, item.line_start, item.line_end)} />
                  ) : null}
                </div>
              ))}
            </Card>
          ) : null}

          {analysis.unknowns?.length ? (
            <Card id="questions" title="Open questions" badge="ai" note="What a newcomer will need that RepoLens couldn't determine. Ask the team about these.">
              <BulletList items={analysis.unknowns} />
            </Card>
          ) : null}

          <Card title="Supporting evidence" badge="verified" note="Quotes behind the overview, each matched exactly against its source file.">
            {analysis.evidence.length ? (
              <ul className="list">
                {analysis.evidence.map((item, index) => (
                  <li key={`${item.path}-${index}`}>
                    {item.claim}
                    <Quote quote={item.quote} href={sourceUrl(item.path, item.line_start, item.line_end)} label={lineLabel(item.path, item.line_start, item.line_end)} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="card-note">No source excerpts could be verified for this analysis.</p>
            )}
          </Card>
        </div>

        <aside className="column">
          {facts ? (
            <Card title="Entry points" badge="verified" note="Detected from file names and package manifests.">
              {facts.entry_points.length ? (
                <ul className="list">
                  {facts.entry_points.map((entry) => (
                    <li key={entry.path}>
                      <a href={sourceUrl(entry.path)} target="_blank" rel="noreferrer" className="path">{entry.path}</a>
                      <div className="sub">{entry.reason}</div>
                    </li>
                  ))}
                </ul>
              ) : <p className="card-note">No conventional entry points were found.</p>}
            </Card>
          ) : null}

          <Card title="Components" badge="ai"><BulletList items={analysis.main_components} /></Card>
          <Card title="Folders" badge="ai"><BulletList items={analysis.folder_responsibilities} /></Card>
          <Card title="Technologies" badge="ai"><BulletList items={analysis.technologies_used} /></Card>
          {facts ? <CoverageCard coverage={facts.coverage} /> : null}
        </aside>
      </div>
    </div>
  );
}

function RepositoryHeader({ repository }: { repository: RepositoryDetails }) {
  return (
    <section className="card repo-header" aria-label="Repository details">
      <div className="repo-title">
        <h2>
          <span className="owner">{repository.owner} / </span>
          <a href={`https://github.com/${repository.owner}/${repository.name}`} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>
            {repository.name}
          </a>
        </h2>
        <span className="badge verified">Public</span>
      </div>
      {repository.description ? <p className="prose">{repository.description}</p> : null}
      <div className="stats">
        <span><strong>{numberFormatter.format(repository.stars)}</strong> stars</span>
        <span><strong>{numberFormatter.format(repository.forks)}</strong> forks</span>
        <span>Branch <strong>{repository.default_branch}</strong></span>
        {repository.primary_language ? <span>Mostly <strong>{repository.primary_language}</strong></span> : null}
      </div>
      {repository.languages.length ? (
        <>
          <div className="lang-bar" aria-hidden="true">
            {repository.languages.map((language, index) => (
              <span key={language.name} style={{ width: `${language.percentage}%`, background: languageColors[index % languageColors.length] }} />
            ))}
          </div>
          <div className="lang-legend">
            {repository.languages.slice(0, 7).map((language, index) => (
              <span key={language.name} style={{ ['--dot' as string]: languageColors[index % languageColors.length] }}>
                {language.name} {language.percentage}%
              </span>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

function Card({ id, title, badge, note, children }: { id?: string; title: string; badge: 'verified' | 'ai'; note?: string; children: ReactNode }) {
  return (
    <section id={id} aria-label={title} className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <span className={`badge ${badge}`}>{badge === 'verified' ? 'From the repo' : 'AI generated'}</span>
      </div>
      {note ? <p className="card-note">{note}</p> : null}
      {children}
    </section>
  );
}

function CommandList({ commands, sourceUrl }: { commands: RunCommand[]; sourceUrl: SourceUrl }) {
  if (!commands.length) {
    return <p className="card-note">No setup or run commands were found in the inspected files. See Open questions.</p>;
  }
  const groups = new Map<string, RunCommand[]>();
  commands.forEach((command) => {
    groups.set(command.source_path, [...(groups.get(command.source_path) ?? []), command]);
  });

  return (
    <div>
      {[...groups.entries()].map(([path, group]) => (
        <div key={path} className="cmd-group">
          <p className="cmd-source">
            From <a href={sourceUrl(path)} target="_blank" rel="noreferrer" className="path">{path}</a>
          </p>
          {group.map((command, index) => (
            <div key={`${command.command}-${index}`} className="cmd">
              <span className="cmd-kind">{commandKindLabels[command.kind]}</span>
              <div>
                <code>{command.command}</code>
                <div className="cmd-meta">
                  {command.working_directory !== '.' ? <>in <code>{command.working_directory}/</code> · </> : null}
                  <a href={sourceUrl(command.source_path, command.line)} target="_blank" rel="noreferrer">
                    {command.inferred ? 'inferred from this file' : `line ${command.line}`}
                  </a>
                </div>
              </div>
              <CopyButton text={command.command} />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="copy"
      aria-label={`Copy ${text}`}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }).catch(() => undefined);
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function CoverageCard({ coverage }: { coverage: RepositoryFacts['coverage'] }) {
  const tree = coverage.tree_entries_total
    ? `${coverage.tree_entries_inspected} of ${coverage.tree_entries_total} paths in the file tree`
    : `${coverage.tree_entries_inspected} paths in the file tree${coverage.tree_truncated ? ' (the full tree is larger)' : ''}`;
  return (
    <section aria-label="What RepoLens read" className="card coverage">
      <div className="card-head"><h2>What RepoLens read</h2></div>
      <ul className="list">
        <li>README {coverage.readme_found ? (coverage.readme_truncated ? 'read (truncated)' : 'read in full') : 'not found'}</li>
        <li>{tree}</li>
        <li>
          {coverage.source_files_inspected.length} source/config files
          {coverage.source_files_skipped ? `, ${coverage.source_files_skipped} others not read` : ''}
          {coverage.source_files_inspected.length ? (
            <details style={{ marginTop: 6 }}>
              <summary>Show files</summary>
              <ul className="list" style={{ marginTop: 6 }}>
                {coverage.source_files_inspected.map((path) => <li key={path} className="path">{path}</li>)}
              </ul>
            </details>
          ) : null}
        </li>
      </ul>
      <p className="card-note">Anything outside these files is unknown to RepoLens.</p>
    </section>
  );
}

function Quote({ quote, href, label }: { quote: string; href: string; label: string }) {
  return (
    <div>
      <pre className="quote">{quote}</pre>
      <a href={href} target="_blank" rel="noreferrer" className="path" style={{ fontSize: '0.8rem' }}>{label}</a>
    </div>
  );
}

function BulletList({ items }: { items: string[] }) {
  return items.length ? (
    <ul className="list">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
  ) : (
    <p className="card-note">Nothing identified.</p>
  );
}

function LoadingState({ stage }: { stage: number }) {
  return (
    <section aria-live="polite" aria-busy="true" className="card loading">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="spinner" aria-hidden="true" />
        <strong>Analyzing repository…</strong>
      </div>
      <ul className="stage-list">
        {loadingStages.map((label, index) => (
          <li key={label} className={index < stage ? 'done' : index === stage ? 'active' : ''}>
            <span className="stage-dot" aria-hidden="true" />
            {label}
          </li>
        ))}
      </ul>
      <div style={{ display: 'grid', gap: 8 }} aria-hidden="true">
        <div className="skeleton" style={{ width: '90%' }} />
        <div className="skeleton" style={{ width: '75%' }} />
        <div className="skeleton" style={{ width: '82%' }} />
      </div>
    </section>
  );
}

function Features() {
  const features: [ReactNode, string, string][] = [
    [<TerminalIcon key="t" />, 'Real commands, not guesses', 'Setup, run, and test commands come straight from package.json, Makefiles, pyproject.toml, and the README, each linked to its line.'],
    [<MapIcon key="m" />, 'Know where to start', 'Entry points and a suggested reading order, so you open the right five files instead of fifty.'],
    [<CheckIcon key="c" />, 'Honest about its limits', 'Every quote is checked against the source, AI output is labeled, and RepoLens lists what it could not inspect.'],
  ];
  return (
    <section className="features" aria-label="How RepoLens helps">
      {features.map(([icon, title, body]) => (
        <div key={title} className="feature">
          <span className="feature-icon" aria-hidden="true">{icon}</span>
          <h3>{title}</h3>
          <p>{body}</p>
        </div>
      ))}
    </section>
  );
}

function normalizeRepositoryUrl(input: string): string | null {
  const trimmed = input.trim().replace(/\.git$/, '').replace(/\/+$/, '');
  const match =
    trimmed.match(/^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)(?:\/.*)?$/) ??
    trimmed.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  return match ? `https://github.com/${match[1]}/${match[2]}` : null;
}

function lineLabel(path: string, lineStart: number, lineEnd: number) {
  return lineStart === lineEnd ? `${path}:${lineStart}` : `${path}:${lineStart}-${lineEnd}`;
}

function isRepositoryAnalysisResult(value: unknown): value is RepositoryAnalysisResult {
  if (typeof value !== 'object' || value === null || !('repository' in value) || !('analysis' in value)) {
    return false;
  }
  const analysis = value.analysis;
  return typeof analysis === 'object' && analysis !== null && 'executive_summary' in analysis && 'architecture_overview' in analysis;
}

const iconProps = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };

function LensIcon() {
  return <svg {...iconProps} width={15} height={15} strokeWidth={2.5}><circle cx="11" cy="11" r="6" /><path d="m20 20-4.5-4.5" /></svg>;
}

function GlobeIcon() {
  return <svg {...iconProps} width={13} height={13}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>;
}

function LockIcon() {
  return <svg {...iconProps}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>;
}

function TerminalIcon() {
  return <svg {...iconProps}><path d="m4 17 6-5-6-5M12 19h8" /></svg>;
}

function MapIcon() {
  return <svg {...iconProps}><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14" /></svg>;
}

function CheckIcon() {
  return <svg {...iconProps}><path d="M20 6 9 17l-5-5" /></svg>;
}

function GitHubIcon() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.56 9.56 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2z" />
    </svg>
  );
}
