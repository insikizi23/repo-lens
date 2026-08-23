'use client';

import { FormEvent, useState } from 'react';

interface RepositoryAnalysis {
  executive_summary: string;
  technologies_used: string[];
  architecture_overview: string;
  main_components: string[];
  folder_responsibilities: string[];
  suggested_improvements: string[];
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
  analysis: RepositoryAnalysis;
}

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000';

const loadingStages = [
  'Retrieving the public repository from GitHub',
  'Reading metadata, README, and file tree',
  'Analyzing the repository architecture',
  'Generating engineering insights',
];

const numberFormatter = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

export default function HomePage() {
  const [repositoryUrl, setRepositoryUrl] = useState('');
  const [repository, setRepository] = useState<RepositoryDetails | null>(null);
  const [analysis, setAnalysis] = useState<RepositoryAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState(0);
  const [error, setError] = useState('');

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedUrl = repositoryUrl.trim();

    if (!normalizedUrl) {
      setError('Enter a public GitHub repository URL to begin.');
      return;
    }

    setLoading(true);
    setLoadingStage(0);
    setError('');
    setRepository(null);
    setAnalysis(null);

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
            : 'Unable to analyze this repository. Please try again.';
        throw new Error(detail);
      }

      const data: unknown = await response.json();
      if (isRepositoryAnalysisResult(data)) {
        setRepository(data.repository);
        setAnalysis(data.analysis);
      } else if (isRepositoryAnalysis(data)) {
        // Supports an already-running older backend until it is restarted.
        setAnalysis(data);
      } else {
        throw new Error('The backend returned an unexpected analysis response. Restart the backend and try again.');
      }
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Unexpected error while analyzing the repository.');
    } finally {
      stageTimers.forEach((timer) => window.clearTimeout(timer));
      setLoading(false);
    }
  }

  return (
    <main style={{ minHeight: '100vh', padding: '2rem', background: 'linear-gradient(135deg, #020617 0%, #0f172a 100%)' }}>
      <div style={{ maxWidth: '1100px', margin: '0 auto', display: 'grid', gap: '2rem' }}>
        <section style={{ display: 'grid', gap: '1rem' }}>
          <p style={{ margin: 0, color: '#38bdf8', fontWeight: 700, letterSpacing: '0.2em', textTransform: 'uppercase' }}>RepoLens</p>
          <h1 style={{ margin: 0, fontSize: '3rem', lineHeight: 1.1 }}>Understand any repository in minutes.</h1>
          <p style={{ margin: 0, fontSize: '1.1rem', maxWidth: '700px', color: '#cbd5e1' }}>
            Paste a public GitHub repository URL and receive a senior-engineer style breakdown based on its actual metadata, README, and file tree.
          </p>
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', marginTop: '1rem' }}>
            <input
              value={repositoryUrl}
              onChange={(event) => setRepositoryUrl(event.target.value)}
              placeholder="https://github.com/owner/repository"
              aria-label="Public GitHub repository URL"
              disabled={loading}
              style={{ flex: '1 1 320px', padding: '0.9rem 1rem', borderRadius: '0.75rem', border: '1px solid #334155', background: '#0f172a', color: '#f8fafc' }}
            />
            <button type="submit" disabled={loading} style={{ padding: '0.9rem 1.4rem', borderRadius: '0.75rem', border: 'none', background: '#38bdf8', color: '#082f49', fontWeight: 700, cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.75 : 1 }}>
              {loading ? 'Analyzing...' : 'Analyze Repository'}
            </button>
          </form>
          {error ? <p role="alert" style={{ margin: 0, color: '#fda4af' }}>{error}</p> : null}
        </section>

        {loading ? <LoadingState stage={loadingStage} /> : null}

        {repository ? <RepositoryOverview repository={repository} /> : null}

        {analysis ? (
          <section aria-label="AI analysis" style={{ display: 'grid', gap: '1.25rem' }}>
            <div style={{ background: '#111827', border: '1px solid #334155', borderRadius: '1rem', padding: '1.5rem' }}>
              <p style={{ margin: '0 0 0.5rem', color: '#38bdf8', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', fontSize: '0.8rem' }}>AI Analysis</p>
              <h2 style={{ marginTop: 0 }}>Executive Summary</h2>
              <p style={{ color: '#cbd5e1', lineHeight: 1.7, marginBottom: 0 }}>{analysis.executive_summary}</p>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem' }}>
              <Card title="Technologies Used" items={analysis.technologies_used} />
              <Card title="Architecture Overview" items={[analysis.architecture_overview]} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem' }}>
              <Card title="Main Components" items={analysis.main_components} />
              <Card title="Folder Responsibilities" items={analysis.folder_responsibilities} />
            </div>
            <Card title="Suggested Improvements" items={analysis.suggested_improvements} />
          </section>
        ) : null}
      </div>
    </main>
  );
}

function LoadingState({ stage }: { stage: number }) {
  return (
    <section aria-live="polite" aria-busy="true" style={{ background: '#111827', border: '1px solid #334155', borderRadius: '1rem', padding: '1.25rem', display: 'grid', gap: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <span aria-hidden="true" style={{ width: '1rem', height: '1rem', border: '2px solid #334155', borderTopColor: '#38bdf8', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.8s linear infinite' }} />
        <div>
          <strong>Analyzing repository...</strong>
          <p style={{ margin: '0.25rem 0 0', color: '#cbd5e1' }}>{loadingStages[stage]}</p>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.5rem' }}>
        {loadingStages.map((label, index) => (
          <span key={label} style={{ color: index <= stage ? '#7dd3fc' : '#64748b', fontSize: '0.9rem' }}>
            {index === stage ? '• ' : '○ '}{label}
          </span>
        ))}
      </div>
    </section>
  );
}

function isRepositoryAnalysis(value: unknown): value is RepositoryAnalysis {
  return (
    typeof value === 'object' &&
    value !== null &&
    'executive_summary' in value &&
    'technologies_used' in value &&
    'architecture_overview' in value
  );
}

function isRepositoryAnalysisResult(value: unknown): value is RepositoryAnalysisResult {
  return (
    typeof value === 'object' &&
    value !== null &&
    'repository' in value &&
    'analysis' in value &&
    isRepositoryAnalysis(value.analysis)
  );
}

function RepositoryOverview({ repository }: { repository: RepositoryDetails }) {
  return (
    <section aria-label="Repository details" style={{ background: '#111827', border: '1px solid #334155', borderRadius: '1rem', padding: '1.5rem', display: 'grid', gap: '1.25rem' }}>
      <div>
        <p style={{ margin: '0 0 0.5rem', color: '#38bdf8', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', fontSize: '0.8rem' }}>Repository</p>
        <h2 style={{ margin: 0 }}>{repository.owner}/{repository.name}</h2>
        <p style={{ margin: '0.65rem 0 0', color: '#cbd5e1', lineHeight: 1.6 }}>{repository.description || 'No description provided by this repository.'}</p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.75rem' }}>
        <Fact label="Stars" value={numberFormatter.format(repository.stars)} />
        <Fact label="Forks" value={numberFormatter.format(repository.forks)} />
        <Fact label="Primary language" value={repository.primary_language || 'Unknown'} />
        <Fact label="Default branch" value={repository.default_branch} />
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {repository.languages.length ? repository.languages.map((language) => (
          <span key={language.name} style={{ border: '1px solid #334155', background: '#0f172a', borderRadius: '999px', color: '#cbd5e1', padding: '0.35rem 0.65rem', fontSize: '0.9rem' }}>
            {language.name} {language.percentage}%
          </span>
        )) : <span style={{ color: '#94a3b8' }}>GitHub did not report language data.</span>}
      </div>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: '#0f172a', borderRadius: '0.75rem', padding: '0.85rem 1rem' }}>
      <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.8rem' }}>{label}</p>
      <p style={{ margin: '0.3rem 0 0', fontWeight: 700 }}>{value}</p>
    </div>
  );
}

function Card({ title, items }: { title: string; items: string[] }) {
  return (
    <div style={{ background: '#111827', border: '1px solid #334155', borderRadius: '1rem', padding: '1.25rem' }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <ul style={{ margin: 0, paddingLeft: '1.2rem', color: '#cbd5e1', display: 'grid', gap: '0.45rem' }}>
        {items.map((item, index) => (
          <li key={`${item}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
