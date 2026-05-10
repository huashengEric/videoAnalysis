import { useEffect, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function buildApi(token) {
  const inst = axios.create({ baseURL: API_BASE })
  if (token) inst.defaults.headers.common['Authorization'] = `Bearer ${token}`
  return inst
}

// ── Login / Register Screen ──────────────────────────

function AuthScreen({ onLogin }) {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!username.trim() || !password) { setError('用户名和密码不能为空'); return }
    setLoading(true); setError('')
    try {
      const url = mode === 'login' ? `${API_BASE}/api/auth/login` : `${API_BASE}/api/auth/register`
      const payload = mode === 'login'
        ? { username: username.trim(), password }
        : { username: username.trim(), password, display_name: displayName.trim() }
      const res = await axios.post(url, payload)
      onLogin(res.data.token, res.data.user)
    } catch (e) {
      const d = e.response?.data?.detail
      setError(typeof d === 'string' ? d : mode === 'login' ? '登录失败，请检查用户名和密码' : '注册失败，用户名可能已存在')
    } finally { setLoading(false) }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-logo">
          <span className="auth-logo-icon">📊</span>
          <span className="auth-logo-title">视频内容分析</span>
        </div>
        <h2 className="auth-title">{mode === 'login' ? '登录账户' : '创建账户'}</h2>
        <form className="auth-form" onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">用户名</label>
            <input className="form-input" type="text" autoComplete="username" placeholder="输入用户名"
              value={username} onChange={e => setUsername(e.target.value)} />
          </div>
          {mode === 'register' && (
            <div className="form-group">
              <label className="form-label">显示名称（可选）</label>
              <input className="form-input" type="text" placeholder="如：张三"
                value={displayName} onChange={e => setDisplayName(e.target.value)} />
            </div>
          )}
          <div className="form-group">
            <label className="form-label">密码</label>
            <input className="form-input" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} placeholder="输入密码"
              value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          {error && <div className="auth-error">{error}</div>}
          <button className="btn btn-primary auth-submit" type="submit" disabled={loading}>
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册'}
          </button>
        </form>
        <div className="auth-switch">
          {mode === 'login'
            ? <span>还没有账户？<button className="auth-link" onClick={() => { setMode('register'); setError('') }}>注册</button></span>
            : <span>已有账户？<button className="auth-link" onClick={() => { setMode('login'); setError('') }}>登录</button></span>
          }
        </div>
      </div>
    </div>
  )
}

// ── Main App ─────────────────────────────────────────

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('auth_token') || '')
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)

  const [creators, setCreators] = useState([])
  const [loadingCreators, setLoadingCreators] = useState(false)
  const [selectedCreatorId, setSelectedCreatorId] = useState(null)
  const [videos, setVideos] = useState([])
  const [loadingVideos, setLoadingVideos] = useState(false)
  const [videosPage, setVideosPage] = useState(1)
  const [videosHasMore, setVideosHasMore] = useState(false)
  const [videosPageSize] = useState(10)
  const [analyzingUrl, setAnalyzingUrl] = useState('')
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('analyze')
  const [activeResult, setActiveResult] = useState(null)
  const [loadingResultId, setLoadingResultId] = useState(null)
  const [activeReadable, setActiveReadable] = useState(null)
  const [loadingReadableId, setLoadingReadableId] = useState(null)
  const [editingCreatorId, setEditingCreatorId] = useState(null)
  const [schedules, setSchedules] = useState([])
  const [loadingSchedules, setLoadingSchedules] = useState(false)
  const [scheduleForm, setScheduleForm] = useState({
    name: '每日自动拉取分析',
    enabled: true,
    time: '09:00',
    max_new_per_creator: 5,
    creator_indices: [],
    report_email: '',
    generate_summary: true,
  })
  const [selectedScheduleId, setSelectedScheduleId] = useState(null)
  const [scheduleRuns, setScheduleRuns] = useState([])
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [form, setForm] = useState({ name: '', url: '', max_new_videos: 5, platform: 'douyin' })

  const api = buildApi(token)

  // ── verify token on mount ──
  useEffect(() => {
    if (!token) { setAuthChecked(true); return }
    api.get('/api/auth/me').then(res => {
      setUser(res.data); setAuthChecked(true)
    }).catch(() => {
      localStorage.removeItem('auth_token'); setToken(''); setAuthChecked(true)
    })
  }, [])

  const handleLogin = (newToken, newUser) => {
    localStorage.setItem('auth_token', newToken)
    setToken(newToken); setUser(newUser)
  }

  const handleLogout = async () => {
    try { await api.post('/api/auth/logout') } catch {}
    localStorage.removeItem('auth_token')
    setToken(''); setUser(null)
    setCreators([]); setVideos([]); setSchedules([])
    setSelectedCreatorId(null)
  }

  // ── show login if not authenticated ──
  if (!authChecked) return null
  if (!token || !user) return <AuthScreen onLogin={handleLogin} />

  // ── helpers ──

  const formatCreateTime = (ts) => {
    const n = Number(ts)
    if (!Number.isFinite(n) || n <= 0) return ''
    const d = new Date(n * 1000)
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
  }

  const normalizeMarkdown = (raw) => String(raw || '').replace(/\r\n/g, '\n').trim()
  const getInitials = (name) => (name || '?').slice(0, 2).toUpperCase()

  // ── data loaders ──

  const loadCreators = async () => {
    try {
      setLoadingCreators(true); setError('')
      const res = await api.get('/api/creators')
      setCreators(res.data || [])
    } catch { setError('加载博主列表失败，请检查后端是否已启动。') }
    finally { setLoadingCreators(false) }
  }

  const loadSchedules = async () => {
    try {
      setLoadingSchedules(true); setError('')
      const res = await api.get('/api/schedules')
      setSchedules(res.data || [])
    } catch { setError('加载定时任务失败。') }
    finally { setLoadingSchedules(false) }
  }

  const loadScheduleRuns = async (scheduleId) => {
    if (!scheduleId) return
    try {
      setLoadingRuns(true); setError('')
      const res = await api.get(`/api/schedules/${scheduleId}/runs`)
      setScheduleRuns(res.data?.runs || [])
    } catch { setError('加载运行记录失败。') }
    finally { setLoadingRuns(false) }
  }

  const upsertSchedule = async () => {
    const [hourStr, minuteStr] = String(scheduleForm.time || '09:00').split(':')
    const hour = Number(hourStr); const minute = Number(minuteStr)
    const allSelected = scheduleForm.creator_indices.length === creators.length
    const payload = {
      name: String(scheduleForm.name || '').trim(),
      enabled: Boolean(scheduleForm.enabled),
      hour: Number.isFinite(hour) ? hour : 9,
      minute: Number.isFinite(minute) ? minute : 0,
      max_new_per_creator: Number(scheduleForm.max_new_per_creator) || 5,
      creator_indices: allSelected ? [] : (scheduleForm.creator_indices || []),
      report_email: String(scheduleForm.report_email || '').trim() || null,
      generate_summary: Boolean(scheduleForm.generate_summary),
    }
    if (!payload.name) { alert('任务名称不能为空'); return }
    try {
      setError('')
      if (selectedScheduleId) await api.put(`/api/schedules/${selectedScheduleId}`, payload)
      else await api.post('/api/schedules', payload)
      await loadSchedules(); alert('已保存')
    } catch { setError('保存定时任务失败。') }
  }

  const deleteSchedule = async (scheduleId) => {
    if (!window.confirm('确定要删除这个定时任务吗？')) return
    try {
      setError('')
      await api.delete(`/api/schedules/${scheduleId}`)
      if (selectedScheduleId === scheduleId) { setSelectedScheduleId(null); setScheduleRuns([]) }
      await loadSchedules()
    } catch { setError('删除定时任务失败。') }
  }

  const runScheduleNow = async (scheduleId) => {
    try {
      setError('')
      await api.post(`/api/schedules/${scheduleId}/run`)
      alert('已触发执行（后台运行中）')
      await loadScheduleRuns(scheduleId)
    } catch { setError('触发执行失败。') }
  }

  const loadVideos = async (creatorId, page = 1) => {
    try {
      setSelectedCreatorId(creatorId); setLoadingVideos(true); setError(''); setVideos([]); setVideosPage(page)
      const res = await api.get(`/api/creators/${creatorId}/videos`, { params: { page, page_size: videosPageSize } })
      const data = res.data
      const items = Array.isArray(data) ? data : data?.items
      setVideos(items || [])
      setVideosHasMore(Boolean(!Array.isArray(data) && data?.has_more))
    } catch { setError('加载视频列表失败。') }
    finally { setLoadingVideos(false) }
  }

  const analyzeVideo = async (video) => {
    try {
      setAnalyzingUrl(video.video_url); setError('')
      const analysisMode = String(video.platform || video.source_platform || '').toLowerCase() === 'youtube' || /youtube\.com|youtu\.be/i.test(String(video.video_url || '')) ? 'youtube' : 'douyin'
      await api.post('/api/analyze', { video_url: video.video_url, aweme_id: video.aweme_id, analysis_mode: analysisMode })
      if (selectedCreatorId !== null) await loadVideos(selectedCreatorId, videosPage)
      await openResult(video)
    } catch { setError('分析失败，请查看后端日志。') }
    finally { setAnalyzingUrl('') }
  }

  const openReadable = async (video, force = false) => {
    try {
      setLoadingReadableId(video.aweme_id); setError('')
      const res = await api.post('/api/video/readable-transcript', { aweme_id: video.aweme_id, force: Boolean(force) })
      setActiveReadable({ aweme_id: video.aweme_id, title: video.title, text: res.data?.text ?? '', cached: Boolean(res.data?.cached) })
    } catch (e) {
      const d = e.response?.data?.detail
      setError(typeof d === 'string' ? d : '全文阅读加载失败。')
    } finally { setLoadingReadableId(null) }
  }

  const openResult = async (video) => {
    try {
      setLoadingResultId(video.aweme_id); setError('')
      const res = await api.get('/api/video/result', { params: { aweme_id: video.aweme_id } })
      setActiveResult({ title: video.title, markdown: normalizeMarkdown(res.data?.content_body ?? res.data?.content), ...res.data })
    } catch { setError('加载分析结果失败。') }
    finally { setLoadingResultId(null) }
  }

  const selectForEdit = (creator) => {
    setEditingCreatorId(creator.id)
    setForm({ name: creator.name || '', url: creator.url || '', max_new_videos: creator.max_new_videos ?? 5, platform: creator.platform || 'douyin' })
  }

  const resetFormForCreate = () => {
    setEditingCreatorId(null)
    setForm({ name: '', url: '', max_new_videos: 5, platform: 'douyin' })
  }

  const handleFormChange = (field, value) => {
    setForm(prev => ({
      ...prev,
      [field]: field === 'max_new_videos' ? Number(value || 0) : field === 'platform' ? String(value || 'douyin') : value,
    }))
  }

  const saveCreator = async () => {
    if (!form.name.trim() || !form.url.trim()) { alert('名称和链接不能为空'); return }
    try {
      setError('')
      const payload = { name: form.name.trim(), url: form.url.trim(), max_new_videos: Number(form.max_new_videos) || 5, platform: form.platform || 'douyin' }
      if (editingCreatorId == null) await api.post('/api/creators', payload)
      else await api.put(`/api/creators/${editingCreatorId}`, payload)
      await loadCreators(); alert('保存成功')
    } catch { setError('保存博主配置失败。') }
  }

  const deleteCreator = async (creatorId) => {
    if (!window.confirm('确定要删除这个博主吗？')) return
    try {
      setError('')
      await api.delete(`/api/creators/${creatorId}`)
      await loadCreators()
      if (editingCreatorId === creatorId) resetFormForCreate()
      alert('已删除')
    } catch { setError('删除博主失败。') }
  }

  useEffect(() => { loadCreators() }, [])

  useEffect(() => {
    if (activeTab === 'schedule') { loadCreators(); loadSchedules() }
  }, [activeTab])

  useEffect(() => {
    if (activeTab === 'schedule' && creators.length > 0 && scheduleForm.creator_indices.length === 0)
      setScheduleForm(p => ({ ...p, creator_indices: creators.map(c => c.id) }))
  }, [activeTab, creators])

  useEffect(() => {
    if (selectedScheduleId) loadScheduleRuns(selectedScheduleId)
  }, [selectedScheduleId])

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return
      if (activeReadable) setActiveReadable(null)
      else if (activeResult) setActiveResult(null)
    }
    if (activeResult || activeReadable) window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeResult, activeReadable])

  const tabMeta = {
    analyze: { icon: '📊', label: '内容分析' },
    manage: { icon: '👥', label: '博主管理' },
    schedule: { icon: '⏰', label: '定时任务' },
  }

  const selectedCreator = creators.find(c => c.id === selectedCreatorId)

  return (
    <div className="app-root">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <p className="sidebar-brand-title">视频内容分析</p>
          <p className="sidebar-brand-sub">抖音 · YouTube · AI 分析</p>
        </div>

        <nav className="sidebar-nav">
          {Object.entries(tabMeta).map(([key, meta]) => (
            <button key={key} className={`nav-item${activeTab === key ? ' active' : ''}`} onClick={() => setActiveTab(key)}>
              <span className="nav-icon">{meta.icon}</span>
              {meta.label}
            </button>
          ))}
        </nav>

        {activeTab === 'analyze' && (
          <>
            <div className="sidebar-divider" />
            <div className="sidebar-section-label">关注的博主</div>
            <div className="sidebar-creators">
              <button className="sidebar-refresh-btn" onClick={loadCreators} disabled={loadingCreators}>
                {loadingCreators ? '🔄 刷新中...' : '↻ 刷新列表'}
              </button>
              {creators.map((c) => {
                const platform = (c.platform || 'douyin').toLowerCase()
                return (
                  <div
                    key={c.id}
                    className={`sidebar-creator-item${selectedCreatorId === c.id ? ' active' : ''}`}
                    onClick={() => loadVideos(c.id)}
                  >
                    <div className={`creator-avatar ${platform}`}>{getInitials(c.name)}</div>
                    <div className="sidebar-creator-info">
                      <div className="sidebar-creator-name">{c.name}</div>
                      <div className="sidebar-creator-platform">{platform === 'youtube' ? 'YouTube' : '抖音'} · 最多 {c.max_new_videos} 条</div>
                    </div>
                  </div>
                )
              })}
              {creators.length === 0 && !loadingCreators && (
                <div className="muted" style={{ padding: '8px 4px', fontSize: 11 }}>暂无博主，请在「博主管理」中添加。</div>
              )}
            </div>
          </>
        )}

        {/* ── User info + logout ── */}
        <div className="sidebar-user">
          <div className="sidebar-user-info">
            <div className="sidebar-user-name">{user?.display_name || user?.username}</div>
            {user?.is_admin && <span className="badge badge-admin">管理员</span>}
          </div>
          <button className="btn btn-secondary btn-sm sidebar-logout" onClick={handleLogout} title="退出登录">
            退出
          </button>
        </div>
      </aside>

      {/* ── Main ── */}
      <div className="main-content">
        <header className="topbar">
          <div className="topbar-title">
            <span className="topbar-title-icon">{tabMeta[activeTab].icon}</span>
            {tabMeta[activeTab].label}
            {activeTab === 'analyze' && selectedCreator && (
              <span style={{ fontSize: 13, fontWeight: 500, color: '#6366f1', marginLeft: 4 }}>
                · {selectedCreator.name}
              </span>
            )}
          </div>
          <div className="topbar-actions">
            {activeTab === 'manage' && (
              <button className="btn btn-primary btn-sm" onClick={resetFormForCreate}>+ 新增博主</button>
            )}
            {activeTab === 'schedule' && (
              <button className="btn btn-primary btn-sm" onClick={() => {
                setSelectedScheduleId(null); setScheduleRuns([])
                setScheduleForm(prev => ({ ...prev, name: '每日自动拉取分析', enabled: true, time: '09:00', max_new_per_creator: 5, creator_indices: creators.map(c => c.id), report_email: '', generate_summary: true }))
              }}>+ 新建任务</button>
            )}
          </div>
        </header>

        <div className="content-area">
          {error && <div className="error-bar">⚠️ {error}</div>}

          {/* ── 内容分析 Tab ── */}
          {activeTab === 'analyze' && (
            <>
              {selectedCreatorId === null && (
                <div className="empty-state">
                  <div className="empty-icon">👈</div>
                  <p className="empty-title">请在左侧选择一位博主</p>
                  <p className="empty-text">选择后将展示其最近视频，可进行逐条分析</p>
                </div>
              )}

              {selectedCreatorId !== null && (
                <>
                  {/* Pagination */}
                  <div className="pagination">
                    <button className="btn btn-secondary btn-sm" onClick={() => loadVideos(selectedCreatorId, Math.max(1, videosPage - 1))} disabled={loadingVideos || videosPage <= 1}>
                      ← 上一页
                    </button>
                    <span className="page-info">第 <strong>{videosPage}</strong> 页 · 每页 {videosPageSize} 条</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => loadVideos(selectedCreatorId, videosPage + 1)} disabled={loadingVideos || !videosHasMore}>
                      下一页 →
                    </button>
                  </div>

                  {loadingVideos && (
                    <div className="empty-state">
                      <div className="empty-icon">⏳</div>
                      <p className="empty-title">正在加载视频列表...</p>
                    </div>
                  )}

                  {!loadingVideos && videos.length === 0 && (
                    <div className="empty-state">
                      <div className="empty-icon">📭</div>
                      <p className="empty-title">未获取到视频</p>
                      <p className="empty-text">可能是 Cookie 已过期，或该博主暂无公开视频</p>
                    </div>
                  )}

                  <div className="video-grid">
                    {videos.map((v, i) => {
                      const stats = v.stats || {}
                      const isAnalyzed = v.analyzed
                      const publishAt = formatCreateTime(v.create_time)
                      const isTop = Boolean(v.is_top)
                      const isYt = (v.platform || v.source_platform) === 'youtube'
                      return (
                        <div key={v.aweme_id || i} className="video-card">
                          <div className="video-card-header">
                            <div className="video-index">{(videosPage - 1) * videosPageSize + i + 1}</div>
                            <div className="video-title">{v.title || '（无标题）'}</div>
                          </div>

                          <div className="video-meta-row">
                            {isYt ? <span className="badge badge-youtube">YouTube</span> : <span className="badge badge-douyin">抖音</span>}
                            {isTop && <span className="badge badge-top">📌 置顶</span>}
                            {isAnalyzed && <span className="badge badge-analyzed">✓ 已分析</span>}
                            {publishAt && <span className="video-date">{publishAt}</span>}
                          </div>

                          <div>
                            <a href={v.video_url} target="_blank" rel="noreferrer" className="video-link">
                              🔗 {v.video_url}
                            </a>
                          </div>

                          {!isYt && (
                            <div className="video-stats">
                              <div className="stat-item"><span className="stat-icon">👍</span>{(stats.digg_count ?? 0).toLocaleString()}</div>
                              <div className="stat-item"><span className="stat-icon">💬</span>{(stats.comment_count ?? 0).toLocaleString()}</div>
                              <div className="stat-item"><span className="stat-icon">🔁</span>{(stats.share_count ?? 0).toLocaleString()}</div>
                              <div className="stat-item"><span className="stat-icon">⭐</span>{(stats.collect_count ?? 0).toLocaleString()}</div>
                            </div>
                          )}

                          <div className="video-actions">
                            <button className="btn btn-readable btn-sm" onClick={() => openReadable(v, false)} disabled={loadingReadableId === v.aweme_id || !v.aweme_id}>
                              {loadingReadableId === v.aweme_id ? <><span className="loading-spinner" /> 整理中</> : '📄 全文阅读'}
                            </button>
                            <button className="btn btn-analyze btn-sm" onClick={() => analyzeVideo(v)} disabled={analyzingUrl === v.video_url}>
                              {analyzingUrl === v.video_url ? <><span className="loading-spinner" /> 分析中</> : '🤖 AI 分析'}
                            </button>
                            {isAnalyzed && (
                              <button className="btn btn-result btn-sm" onClick={() => openResult(v)} disabled={loadingResultId === v.aweme_id}>
                                {loadingResultId === v.aweme_id ? <><span className="loading-spinner" /> 加载中</> : '📋 查看结果'}
                              </button>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </>
          )}

          {/* ── 博主管理 Tab ── */}
          {activeTab === 'manage' && (
            <div className="manage-layout">
              <div className="card">
                <div className="card-header">
                  <h3 className="card-title">👥 已添加博主 <span style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8' }}>({creators.length})</span></h3>
                  <button className="btn btn-secondary btn-sm" onClick={loadCreators} disabled={loadingCreators}>
                    {loadingCreators ? '刷新中...' : '↻ 刷新'}
                  </button>
                </div>
                <div className="card-body">
                  {creators.length === 0 && !loadingCreators && (
                    <div className="empty-state" style={{ padding: '24px 0' }}>
                      <div className="empty-icon">👤</div>
                      <p className="empty-title">暂无博主</p>
                      <p className="empty-text">在右侧填写信息后保存</p>
                    </div>
                  )}
                  <ul className="creator-manage-list">
                    {creators.map((c) => {
                      const platform = (c.platform || 'douyin').toLowerCase()
                      return (
                        <li key={c.id} className="creator-manage-item">
                          <div className={`creator-avatar ${platform}`} style={{ width: 36, height: 36 }}>{getInitials(c.name)}</div>
                          <div className="creator-manage-info">
                            <div className="creator-manage-name">
                              {c.name}
                              <span className={`badge badge-${platform}`} style={{ marginLeft: 6 }}>{platform === 'youtube' ? 'YouTube' : '抖音'}</span>
                            </div>
                            <div className="creator-manage-url">{c.url}</div>
                          </div>
                          <div className="creator-manage-actions">
                            <button className="btn btn-secondary btn-sm" onClick={() => selectForEdit(c)}>编辑</button>
                            <button className="btn btn-danger btn-sm" onClick={() => deleteCreator(c.id)}>删除</button>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              </div>

              <div className="card">
                <div className="card-header">
                  <h3 className="card-title">{editingCreatorId == null ? '➕ 新增博主' : '✏️ 编辑博主'}</h3>
                  {editingCreatorId != null && <button className="btn btn-secondary btn-sm" onClick={resetFormForCreate}>切换为新增</button>}
                </div>
                <div className="card-body">
                  <div className="form-group">
                    <label className="form-label">博主名称 *</label>
                    <input className="form-input" type="text" placeholder="如：老江更甜" value={form.name} onChange={e => handleFormChange('name', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">平台 *</label>
                    <select className="form-select" value={form.platform || 'douyin'} onChange={e => handleFormChange('platform', e.target.value)}>
                      <option value="douyin">抖音</option>
                      <option value="youtube">YouTube</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">主页链接 *</label>
                    <input className="form-input" type="text"
                      placeholder={form.platform === 'youtube' ? 'https://www.youtube.com/@handle' : 'https://www.douyin.com/user/...'}
                      value={form.url} onChange={e => handleFormChange('url', e.target.value)} />
                    <span className="form-hint">{form.platform === 'youtube' ? '支持 @handle 或 /channel/UC…' : '抖音博主主页完整链接'}</span>
                  </div>
                  <div className="form-group">
                    <label className="form-label">每次最多拉取新视频</label>
                    <input className="form-input" type="number" min="1" value={form.max_new_videos} onChange={e => handleFormChange('max_new_videos', e.target.value)} />
                  </div>
                  <button className="btn btn-success" style={{ width: '100%', justifyContent: 'center' }} onClick={saveCreator}>
                    💾 保存博主
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ── 定时任务 Tab ── */}
          {activeTab === 'schedule' && (
            <div className="manage-layout">
              <div className="card">
                <div className="card-header">
                  <h3 className="card-title">⏰ 定时任务</h3>
                  <button className="btn btn-secondary btn-sm" onClick={loadSchedules} disabled={loadingSchedules}>
                    {loadingSchedules ? '刷新中...' : '↻ 刷新'}
                  </button>
                </div>
                <div className="card-body">
                  {loadingSchedules && <div className="muted" style={{ padding: '12px 0' }}>正在加载任务列表...</div>}
                  {!loadingSchedules && schedules.length === 0 && (
                    <div className="empty-state" style={{ padding: '24px 0' }}>
                      <div className="empty-icon">⏱️</div>
                      <p className="empty-title">暂无定时任务</p>
                      <p className="empty-text">点击右上角「新建任务」</p>
                    </div>
                  )}
                  <ul className="schedule-list">
                    {schedules.map(s => {
                      const timeLabel = `${String(s.hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}`
                      return (
                        <li key={s.id} className={`schedule-item${selectedScheduleId === s.id ? ' active' : ''}`} onClick={() => {
                          setSelectedScheduleId(s.id)
                          const effectiveCreatorIds = (s.creator_indices || []).length === 0 ? creators.map(c => c.id) : s.creator_indices
                          setScheduleForm({ name: s.name, enabled: Boolean(s.enabled), time: timeLabel, max_new_per_creator: s.max_new_per_creator ?? 5, creator_indices: effectiveCreatorIds, report_email: s.report_email || '', generate_summary: Boolean(s.generate_summary) })
                        }}>
                          <div className={`schedule-status-dot ${s.enabled ? 'enabled' : 'disabled'}`} />
                          <div className="schedule-info">
                            <div className="schedule-name">{s.name}</div>
                            <div className="schedule-meta">每日 {timeLabel} · 每博主 {s.max_new_per_creator} 条 · {s.enabled ? '✅ 启用' : '⏸ 停用'}</div>
                          </div>
                          <div className="schedule-actions" onClick={e => e.stopPropagation()}>
                            <button className="btn btn-secondary btn-sm" onClick={() => runScheduleNow(s.id)}>▶ 立即</button>
                            <button className="btn btn-danger btn-sm" onClick={() => deleteSchedule(s.id)}>删除</button>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              </div>

              <div className="card">
                <div className="card-header">
                  <h3 className="card-title">{selectedScheduleId ? '✏️ 编辑任务' : '➕ 新建任务'}</h3>
                  <button className="btn btn-success btn-sm" onClick={upsertSchedule}>{selectedScheduleId ? '保存修改' : '创建任务'}</button>
                </div>
                <div className="card-body">
                  <div className="form-group">
                    <label className="form-label">任务名称</label>
                    <input className="form-input" type="text" value={scheduleForm.name} onChange={e => setScheduleForm(p => ({ ...p, name: e.target.value }))} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">每日执行时间</label>
                    <input className="form-input" type="time" value={scheduleForm.time} onChange={e => setScheduleForm(p => ({ ...p, time: e.target.value }))} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">每个博主最多拉取新视频</label>
                    <input className="form-input" type="number" min="1" max="50" value={scheduleForm.max_new_per_creator} onChange={e => setScheduleForm(p => ({ ...p, max_new_per_creator: Number(e.target.value || 0) }))} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">汇报邮箱（可选）</label>
                    <input className="form-input" type="email" placeholder="me@example.com" value={scheduleForm.report_email} onChange={e => setScheduleForm(p => ({ ...p, report_email: e.target.value }))} />
                    <span className="form-hint">任务结束后发送汇报邮件（需后端配置 SMTP）</span>
                  </div>
                  <div className="form-group">
                    <div className="form-toggle">
                      <input type="checkbox" id="toggle-enabled" checked={scheduleForm.enabled} onChange={e => setScheduleForm(p => ({ ...p, enabled: e.target.checked }))} />
                      <label htmlFor="toggle-enabled" className="form-toggle-label">任务启用</label>
                    </div>
                  </div>
                  <div className="form-group">
                    <div className="form-toggle">
                      <input type="checkbox" id="toggle-summary" checked={scheduleForm.generate_summary} onChange={e => setScheduleForm(p => ({ ...p, generate_summary: e.target.checked }))} />
                      <label htmlFor="toggle-summary" className="form-toggle-label">生成 AI 总结报告</label>
                    </div>
                  </div>
                  <div className="form-group">
                    <label className="form-label">目标博主（已选 {scheduleForm.creator_indices.length}/{creators.length}）</label>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {creators.map((c) => {
                        const checked = scheduleForm.creator_indices.includes(c.id)
                        return (
                          <label key={c.id} className="form-toggle">
                            <input type="checkbox" checked={checked} onChange={e => {
                              const next = new Set(scheduleForm.creator_indices)
                              if (e.target.checked) next.add(c.id); else next.delete(c.id)
                              setScheduleForm(p => ({ ...p, creator_indices: Array.from(next).sort((a, b) => a - b) }))
                            }} />
                            <span className="form-toggle-label" style={{ fontWeight: 600 }}>{c.name}</span>
                          </label>
                        )
                      })}
                    </div>
                  </div>

                  {selectedScheduleId && (
                    <div style={{ marginTop: 16 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#374151', marginBottom: 8 }}>最近运行记录</div>
                      {loadingRuns && <div className="muted">加载中...</div>}
                      {!loadingRuns && scheduleRuns.length === 0 && <div className="muted">暂无运行记录</div>}
                      {scheduleRuns.slice(0, 10).map(r => (
                        <div key={r.id} className="run-record">
                          <div>
                            <div className="run-status">{r.status}</div>
                            {r.detail && <div className="run-detail">{r.detail}</div>}
                          </div>
                          <span className="muted">#{r.id}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 分析结果 Modal ── */}
      {activeResult && (
        <div className="modal-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setActiveResult(null) }}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div className="modal-header-info">
                <p className="modal-label">🤖 AI 分析结果</p>
                <h2 className="modal-title">{activeResult.title}</h2>
              </div>
              <div className="modal-header-actions">
                <button className="btn btn-secondary btn-sm" onClick={() => setActiveResult(null)}>✕ 关闭</button>
              </div>
            </div>
            <div className="modal-body">
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeResult.markdown || ''}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 全文阅读 Modal ── */}
      {activeReadable && (
        <div className="modal-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setActiveReadable(null) }}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div className="modal-header-info">
                <p className="modal-label">📄 全文阅读</p>
                <h2 className="modal-title">{activeReadable.title}</h2>
                {activeReadable.cached && <p className="muted" style={{ marginTop: 4 }}>已缓存本地稿，若需更新点「重新整理」</p>}
              </div>
              <div className="modal-header-actions">
                <button className="btn btn-secondary btn-sm" disabled={loadingReadableId === activeReadable.aweme_id}
                  onClick={() => openReadable({ aweme_id: activeReadable.aweme_id, title: activeReadable.title, video_url: '' }, true)}>
                  {loadingReadableId === activeReadable.aweme_id ? <><span className="loading-spinner" /> 整理中</> : '↻ 重新整理'}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={() => setActiveReadable(null)}>✕ 关闭</button>
              </div>
            </div>
            <div className="modal-body">
              <div className="readable-body">{activeReadable.text || '（无内容）'}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
