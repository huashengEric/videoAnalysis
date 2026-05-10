import { useEffect, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

function App() {
  const [creators, setCreators] = useState([])
  const [loadingCreators, setLoadingCreators] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(null)
  const [videos, setVideos] = useState([])
  const [loadingVideos, setLoadingVideos] = useState(false)
  const [videosPage, setVideosPage] = useState(1)
  const [videosHasMore, setVideosHasMore] = useState(false)
  const [videosPageSize] = useState(10)
  const [analyzingUrl, setAnalyzingUrl] = useState('')
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('analyze') // 'analyze' | 'manage' | 'schedule'
  const [activeResult, setActiveResult] = useState(null)
  const [loadingResultId, setLoadingResultId] = useState(null)
  const [activeReadable, setActiveReadable] = useState(null)
  const [loadingReadableId, setLoadingReadableId] = useState(null)
  // 管理表单相关
  const [editingIndex, setEditingIndex] = useState(null)
  // 定时任务相关
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
  const [form, setForm] = useState({
    name: '',
    url: '',
    max_new_videos: 5,
    platform: 'douyin',
  })

  const api = axios.create({
    baseURL: API_BASE,
  })

  const formatCreateTime = (ts) => {
    const n = Number(ts)
    if (!Number.isFinite(n) || n <= 0) return ''
    const d = new Date(n * 1000)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
  }

  const normalizeMarkdown = (raw) => String(raw || '').replace(/\r\n/g, '\n').trim()

  const loadCreators = async () => {
    try {
      setLoadingCreators(true)
      setError('')
      const res = await api.get('/api/creators')
      setCreators(res.data || [])
    } catch (e) {
      setError('加载博主列表失败，请检查后端是否已启动。')
    } finally {
      setLoadingCreators(false)
    }
  }

  const loadSchedules = async () => {
    try {
      setLoadingSchedules(true)
      setError('')
      const res = await api.get('/api/schedules')
      setSchedules(res.data || [])
    } catch (e) {
      setError('加载定时任务失败。')
    } finally {
      setLoadingSchedules(false)
    }
  }

  const loadScheduleRuns = async (scheduleId) => {
    if (!scheduleId) return
    try {
      setLoadingRuns(true)
      setError('')
      const res = await api.get(`/api/schedules/${scheduleId}/runs`)
      setScheduleRuns(res.data?.runs || [])
    } catch (e) {
      setError('加载运行记录失败。')
    } finally {
      setLoadingRuns(false)
    }
  }

  const upsertSchedule = async () => {
    const [hourStr, minuteStr] = String(scheduleForm.time || '09:00').split(':')
    const hour = Number(hourStr)
    const minute = Number(minuteStr)
    const payload = {
      name: String(scheduleForm.name || '').trim(),
      enabled: Boolean(scheduleForm.enabled),
      hour: Number.isFinite(hour) ? hour : 9,
      minute: Number.isFinite(minute) ? minute : 0,
      max_new_per_creator: Number(scheduleForm.max_new_per_creator) || 5,
      creator_indices:
        Array.isArray(scheduleForm.creator_indices) &&
        creators.length > 0 &&
        scheduleForm.creator_indices.length === creators.length
          ? []
          : Array.isArray(scheduleForm.creator_indices)
            ? scheduleForm.creator_indices
            : [],
      report_email: String(scheduleForm.report_email || '').trim() || null,
      generate_summary: Boolean(scheduleForm.generate_summary),
    }
    if (!payload.name) {
      alert('任务名称不能为空')
      return
    }
    try {
      setError('')
      if (selectedScheduleId) {
        await api.put(`/api/schedules/${selectedScheduleId}`, payload)
      } else {
        await api.post('/api/schedules', payload)
      }
      await loadSchedules()
      alert('已保存')
    } catch (e) {
      setError('保存定时任务失败。')
    }
  }

  const deleteSchedule = async (scheduleId) => {
    if (!window.confirm('确定要删除这个定时任务吗？')) return
    try {
      setError('')
      await api.delete(`/api/schedules/${scheduleId}`)
      if (selectedScheduleId === scheduleId) {
        setSelectedScheduleId(null)
        setScheduleRuns([])
      }
      await loadSchedules()
    } catch (e) {
      setError('删除定时任务失败。')
    }
  }

  const runScheduleNow = async (scheduleId) => {
    try {
      setError('')
      await api.post(`/api/schedules/${scheduleId}/run`)
      alert('已触发执行（后台运行中）')
      await loadScheduleRuns(scheduleId)
    } catch (e) {
      setError('触发执行失败。')
    }
  }

  const loadVideos = async (index, page = 1) => {
    try {
      setSelectedIndex(index)
      setLoadingVideos(true)
      setError('')
      // 切换博主时先清空旧视频，避免误以为仍是上个博主的数据
      setVideos([])
      setVideosPage(page)
      const res = await api.get(`/api/creators/${index}/videos`, {
        params: { page, page_size: videosPageSize },
      })
      const data = res.data
      const items = Array.isArray(data) ? data : data?.items
      setVideos(items || [])
      setVideosHasMore(Boolean(!Array.isArray(data) && data?.has_more))
    } catch (e) {
      setError('加载视频列表失败。')
    } finally {
      setLoadingVideos(false)
    }
  }

  const analyzeVideo = async (video) => {
    try {
      setAnalyzingUrl(video.video_url)
      setError('')
      const analysisMode =
        String(video.platform || video.source_platform || '').toLowerCase() === 'youtube' ||
        /youtube\.com|youtu\.be/i.test(String(video.video_url || ''))
          ? 'youtube'
          : 'douyin'
      await api.post('/api/analyze', {
        video_url: video.video_url,
        aweme_id: video.aweme_id,
        analysis_mode: analysisMode,
      })
      // 分析完成后：刷新列表状态，并直接展示结果（弹窗）
      if (selectedIndex !== null) {
        await loadVideos(selectedIndex, videosPage)
      }
      await openResult(video)
    } catch (e) {
      setError('分析失败，请查看后端日志。')
    } finally {
      setAnalyzingUrl('')
    }
  }
  const openReadable = async (video, force = false) => {
    try {
      setLoadingReadableId(video.aweme_id)
      setError('')
      const res = await api.post('/api/video/readable-transcript', {
        aweme_id: video.aweme_id,
        force: Boolean(force),
      })
      setActiveReadable({
        aweme_id: video.aweme_id,
        title: video.title,
        text: res.data?.text ?? '',
        cached: Boolean(res.data?.cached),
      })
    } catch (e) {
      const d = e.response?.data?.detail
      setError(typeof d === 'string' ? d : '全文阅读加载失败。')
    } finally {
      setLoadingReadableId(null)
    }
  }

  const openResult = async (video) => {
    try {
      setLoadingResultId(video.aweme_id)
      setError('')
      const res = await api.get('/api/video/result', {
        params: { aweme_id: video.aweme_id },
      })
      setActiveResult({
        title: video.title,
        markdown: normalizeMarkdown(res.data?.content_body ?? res.data?.content),
        ...res.data,
      })
    } catch (e) {
      setError('加载分析结果失败。')
    } finally {
      setLoadingResultId(null)
    }
  }
  // 选中某个博主用于管理表单编辑
  const selectForEdit = (idx) => {
    setEditingIndex(idx)
    const c = creators[idx]
    setForm({
      name: c.name || '',
      url: c.url || '',
      max_new_videos: c.max_new_videos ?? 5,
      platform: c.platform || 'douyin',
    })
  }

  const resetFormForCreate = () => {
    setEditingIndex(null)
    setForm({
      name: '',
      url: '',
      max_new_videos: 5,
      platform: 'douyin',
    })
  }

  const handleFormChange = (field, value) => {
    setForm((prev) => ({
      ...prev,
      [field]:
        field === 'max_new_videos'
          ? Number(value || 0)
          : field === 'platform'
            ? String(value || 'douyin')
            : value,
    }))
  }

  const saveCreator = async () => {
    if (!form.name.trim() || !form.url.trim()) {
      alert('名称和链接不能为空')
      return
    }
    try {
      setError('')
      const payload = {
        name: form.name.trim(),
        url: form.url.trim(),
        max_new_videos: Number(form.max_new_videos) || 5,
        platform: form.platform || 'douyin',
      }
      if (editingIndex == null) {
        await api.post('/api/creators', payload)
      } else {
        await api.put(`/api/creators/${editingIndex}`, payload)
      }
      await loadCreators()
      alert('保存成功')
    } catch (e) {
      setError('保存博主配置失败。')
    }
  }

  const deleteCreator = async (idx) => {
    if (!window.confirm('确定要删除这个博主吗？')) return
    try {
      setError('')
      await api.delete(`/api/creators/${idx}`)
      await loadCreators()
      if (editingIndex === idx) {
        resetFormForCreate()
      }
      alert('已删除')
    } catch (e) {
      setError('删除博主失败。')
    }
  }

  useEffect(() => {
    loadCreators()
  }, [])

  useEffect(() => {
    if (activeTab === 'schedule') {
      loadCreators()
      loadSchedules()
    }
  }, [activeTab])

  useEffect(() => {
    // 定时任务：默认全选所有博主（UI 上打勾）
    if (activeTab === 'schedule' && creators.length > 0 && scheduleForm.creator_indices.length === 0) {
      setScheduleForm((p) => ({ ...p, creator_indices: creators.map((_, idx) => idx) }))
    }
  }, [activeTab, creators])

  useEffect(() => {
    if (selectedScheduleId) {
      loadScheduleRuns(selectedScheduleId)
    }
  }, [selectedScheduleId])

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return
      if (activeReadable) setActiveReadable(null)
      else if (activeResult) setActiveResult(null)
    }
    if (activeResult || activeReadable) {
      window.addEventListener('keydown', onKeyDown)
    }
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeResult, activeReadable])

  return (
    <div className="app-root">
      <header className="app-header">
        <div>
          <h1>抖音多博主内容分析</h1>
          <p>本地开发版 · 前端 React · 后端 FastAPI</p>
        </div>
        <div className="env-tip">
          API: <code>{API_BASE}</code>
        </div>
      </header>

      {/* Tab 切换 */}
      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '8px 16px 0' }}>
        <div style={{ display: 'inline-flex', gap: 8, borderRadius: 999, background: '#e5e7eb', padding: 2 }}>
          <button
            className={activeTab === 'analyze' ? 'tab tab-active' : 'tab'}
            onClick={() => setActiveTab('analyze')}
          >
            内容分析
          </button>
          <button
            className={activeTab === 'manage' ? 'tab tab-active' : 'tab'}
            onClick={() => setActiveTab('manage')}
          >
            博主管理
          </button>
          <button
            className={activeTab === 'schedule' ? 'tab tab-active' : 'tab'}
            onClick={() => setActiveTab('schedule')}
          >
            定时任务
          </button>
        </div>
      </div>

      <main className="app-main">
        {error && <div className="error-bar">{error}</div>}

        {/* 分析 Tab */}
        {activeTab === 'analyze' && (
          <>
            <section className="panel panel-creators">
              <div className="panel-header">
                <h2>关注的博主</h2>
                <button onClick={loadCreators} disabled={loadingCreators}>
                  {loadingCreators ? '刷新中...' : '刷新列表'}
                </button>
              </div>
              {creators.length === 0 && !loadingCreators && (
                <p className="muted">creators.json 暂无配置，请在后端项目根目录添加。</p>
              )}
              <ul className="creator-list">
                {creators.map((c, idx) => (
                  <li
                    key={idx}
                    className={
                      'creator-item' + (selectedIndex === idx ? ' creator-item-active' : '')
                    }
                    onClick={() => loadVideos(idx)}
                  >
                    <div className="creator-main">
                      <div className="creator-name">
                        {(c.platform || 'douyin') === 'youtube' ? (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#fef2f2',
                              border: '1px solid #fecaca',
                              color: '#b91c1c',
                            }}
                          >
                            YouTube
                          </span>
                        ) : (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#eff6ff',
                              border: '1px solid #bfdbfe',
                              color: '#1d4ed8',
                            }}
                          >
                            抖音
                          </span>
                        )}
                        {c.name}
                      </div>
                      <div className="creator-url">{c.url}</div>
                    </div>
                    <div className="creator-meta">
                      每次最多新视频：<strong>{c.max_new_videos}</strong>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section className="panel panel-videos">
              <div className="panel-header">
                <h2>最近视频</h2>
                {selectedIndex === null && (
                  <span className="muted">（先在左侧选择一个博主）</span>
                )}
              </div>
              {selectedIndex !== null && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <button
                    type="button"
                    onClick={() => loadVideos(selectedIndex, Math.max(1, videosPage - 1))}
                    disabled={loadingVideos || videosPage <= 1}
                    style={{
                      fontSize: 12,
                      padding: '6px 10px',
                      borderRadius: 10,
                      border: '1px solid #e5e7eb',
                      background: '#fff',
                      cursor: loadingVideos || videosPage <= 1 ? 'default' : 'pointer',
                    }}
                  >
                    上一页
                  </button>
                  <span className="muted" style={{ fontSize: 12 }}>
                    第 <strong>{videosPage}</strong> 页（每页 {videosPageSize} 条）
                  </span>
                  <button
                    type="button"
                    onClick={() => loadVideos(selectedIndex, videosPage + 1)}
                    disabled={loadingVideos || !videosHasMore}
                    style={{
                      fontSize: 12,
                      padding: '6px 10px',
                      borderRadius: 10,
                      border: '1px solid #e5e7eb',
                      background: '#fff',
                      cursor: loadingVideos || !videosHasMore ? 'default' : 'pointer',
                    }}
                  >
                    下一页
                  </button>
                </div>
              )}
              {loadingVideos && <p className="muted">正在加载该博主的视频...</p>}
              {!loadingVideos && videos.length === 0 && selectedIndex !== null && (
                <p className="muted">未获取到视频列表。</p>
              )}
              <div className="video-list">
                {videos.map((v, i) => {
                  const stats = v.stats || {}
                  const isAnalyzed = v.analyzed
                  const publishAt = formatCreateTime(v.create_time)
                  const isTop = Boolean(v.is_top)
                  const isYt = (v.platform || v.source_platform) === 'youtube'
                  return (
                    <div key={v.aweme_id || i} className="video-card">
                      <div className="video-title">
                        <span style={{ marginRight: 6 }}>#{i + 1}</span>
                        {isYt && (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#fef2f2',
                              border: '1px solid #fecaca',
                              color: '#b91c1c',
                            }}
                          >
                            YouTube
                          </span>
                        )}
                        {isTop && (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#fff7ed',
                              border: '1px solid #fed7aa',
                              color: '#9a3412',
                            }}
                          >
                            置顶
                          </span>
                        )}
                        <span>{v.title || '（无标题）'}</span>
                      </div>
                      <div className="video-meta">
                        <a
                          href={v.video_url}
                          target="_blank"
                          rel="noreferrer"
                          className="video-link"
                        >
                          {v.video_url}
                        </a>
                        {publishAt && <span className="muted">发布时间：{publishAt}</span>}
                      </div>
                      <div className="video-stats">
                        {isYt ? (
                          <span className="muted" style={{ fontSize: 12 }}>
                            互动数据：列表来自 RSS，未拉取点赞/评论（可点链接在站内查看）
                          </span>
                        ) : (
                          <>
                            <span>👍 {stats.digg_count ?? 0}</span>
                            <span>💬 {stats.comment_count ?? 0}</span>
                            <span>🔁 {stats.share_count ?? 0}</span>
                            <span>⭐ {stats.collect_count ?? 0}</span>
                          </>
                        )}
                      </div>
                      <div className="video-actions">
                        <button
                          type="button"
                          className="btn btn-readable"
                          onClick={() => openReadable(v, false)}
                          disabled={loadingReadableId === v.aweme_id || !v.aweme_id}
                        >
                          {loadingReadableId === v.aweme_id ? '整理中...' : '全文阅读'}
                        </button>
                        <button
                          className="btn btn-analyze"
                          onClick={() => analyzeVideo(v)}
                          disabled={analyzingUrl === v.video_url}
                        >
                          {analyzingUrl === v.video_url ? '分析中...' : '分析这条视频'}
                        </button>
                        {isAnalyzed && (
                          <button
                            className="btn btn-result"
                            onClick={() => openResult(v)}
                            disabled={loadingResultId === v.aweme_id}
                          >
                            {loadingResultId === v.aweme_id ? '加载中...' : '视频分析结果'}
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          </>
        )}

        {/* 博主管理 Tab */}
        {activeTab === 'manage' && (
          <>
            <section className="panel panel-creators">
              <div className="panel-header">
                <h2>博主管理</h2>
                <button onClick={loadCreators} disabled={loadingCreators}>
                  {loadingCreators ? '刷新中...' : '刷新列表'}
                </button>
              </div>
              <ul className="creator-list">
                {creators.map((c, idx) => (
                  <li key={idx} className="creator-item">
                    <div className="creator-main">
                      <div className="creator-name">
                        {(c.platform || 'douyin') === 'youtube' ? (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#fef2f2',
                              border: '1px solid #fecaca',
                              color: '#b91c1c',
                            }}
                          >
                            YouTube
                          </span>
                        ) : (
                          <span
                            className="badge"
                            style={{
                              marginRight: 6,
                              background: '#eff6ff',
                              border: '1px solid #bfdbfe',
                              color: '#1d4ed8',
                            }}
                          >
                            抖音
                          </span>
                        )}
                        {c.name}
                      </div>
                      <div className="creator-url">{c.url}</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div className="creator-meta">
                        每次最多：<strong>{c.max_new_videos}</strong>
                      </div>
                      <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                        <button
                          style={{
                            fontSize: 11,
                            padding: '3px 12px',
                            borderRadius: 999,
                            border: '1px solid #d1d5db',
                            backgroundColor: '#ffffff',
                            color: '#374151',
                            cursor: 'pointer',
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            whiteSpace: 'nowrap',
                          }}
                          onClick={() => selectForEdit(idx)}
                        >
                          编辑
                        </button>
                        <button
                          style={{
                            fontSize: 11,
                            padding: '3px 12px',
                            backgroundColor: '#ef4444',
                            color: '#fff',
                            borderRadius: 999,
                            border: 'none',
                            cursor: 'pointer',
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            whiteSpace: 'nowrap',
                          }}
                          onClick={() => deleteCreator(idx)}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section className="panel panel-videos">
              <div className="panel-header">
                <h2>{editingIndex == null ? '新增博主' : '编辑博主'}</h2>
                <button onClick={resetFormForCreate}>新增模式</button>
              </div>
              <div className="muted" style={{ marginBottom: 8 }}>
                名称和主页链接为必填项；“每次最多新视频”用于批量分析上限控制。YouTube
                请填频道主页（支持 @handle 或 /channel/UC…）。
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label className="form-field">
                  <span>名称</span>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => handleFormChange('name', e.target.value)}
                  />
                </label>
                <label className="form-field">
                  <span>平台</span>
                  <select
                    value={form.platform || 'douyin'}
                    onChange={(e) => handleFormChange('platform', e.target.value)}
                  >
                    <option value="douyin">抖音</option>
                    <option value="youtube">YouTube</option>
                  </select>
                </label>
                <label className="form-field">
                  <span>主页链接</span>
                  <input
                    type="text"
                    value={form.url}
                    placeholder={
                      form.platform === 'youtube'
                        ? 'https://www.youtube.com/@handle 或 /channel/UC...'
                        : 'https://www.douyin.com/user/...'
                    }
                    onChange={(e) => handleFormChange('url', e.target.value)}
                  />
                </label>
                <label className="form-field">
                  <span>每次最多新视频</span>
                  <input
                    type="number"
                    min="1"
                    value={form.max_new_videos}
                    onChange={(e) => handleFormChange('max_new_videos', e.target.value)}
                  />
                </label>
                <div style={{ marginTop: 8 }}>
                  <button className="form-save-btn" onClick={saveCreator}>
                    保存
                  </button>
                </div>
              </div>
            </section>
          </>
        )}

        {/* 定时任务 Tab */}
        {activeTab === 'schedule' && (
          <>
            <section className="panel panel-creators">
              <div className="panel-header">
                <h2>定时任务</h2>
                <button
                  onClick={() => {
                    setSelectedScheduleId(null)
                    setScheduleRuns([])
                    setScheduleForm((prev) => ({
                      ...prev,
                      name: '每日自动拉取分析',
                      enabled: true,
                      time: '09:00',
                      max_new_per_creator: 5,
                      creator_indices: creators.map((_, idx) => idx),
                      report_email: '',
                      generate_summary: true,
                    }))
                  }}
                >
                  新建任务
                </button>
              </div>
              {loadingSchedules && <div className="muted">正在加载任务列表...</div>}
              {!loadingSchedules && schedules.length === 0 && (
                <div className="muted">暂无任务。你可以点击右上角“新建任务”。</div>
              )}
              {!loadingSchedules && schedules.length > 0 && (
                <ul className="creator-list">
                  {schedules.map((s) => {
                    const timeLabel = `${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`
                    return (
                      <li
                        key={s.id}
                        className={
                          'creator-item' + (selectedScheduleId === s.id ? ' creator-item-active' : '')
                        }
                        onClick={() => {
                          setSelectedScheduleId(s.id)
                          const effectiveCreatorIndices =
                            (s.creator_indices || []).length === 0 ? creators.map((_, idx) => idx) : s.creator_indices
                          setScheduleForm({
                            name: s.name,
                            enabled: Boolean(s.enabled),
                            time: timeLabel,
                            max_new_per_creator: s.max_new_per_creator ?? 5,
                            creator_indices: effectiveCreatorIndices,
                            report_email: s.report_email || '',
                            generate_summary: Boolean(s.generate_summary),
                          })
                        }}
                      >
                        <div className="creator-main">
                          <div className="creator-name">{s.name}</div>
                          <div className="creator-url">
                            {timeLabel} · 每博主 {s.max_new_per_creator} 条 · {s.enabled ? '启用' : '停用'}
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button
                            style={{
                              fontSize: 11,
                              padding: '3px 12px',
                              borderRadius: 999,
                              border: '1px solid #d1d5db',
                              backgroundColor: '#ffffff',
                              color: '#374151',
                              cursor: 'pointer',
                              whiteSpace: 'nowrap',
                            }}
                            onClick={(e) => {
                              e.stopPropagation()
                              runScheduleNow(s.id)
                            }}
                          >
                            立即执行
                          </button>
                          <button
                            style={{
                              fontSize: 11,
                              padding: '3px 12px',
                              backgroundColor: '#ef4444',
                              color: '#fff',
                              borderRadius: 999,
                              border: 'none',
                              cursor: 'pointer',
                              whiteSpace: 'nowrap',
                            }}
                            onClick={(e) => {
                              e.stopPropagation()
                              deleteSchedule(s.id)
                            }}
                          >
                            删除
                          </button>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>

            <section className="panel panel-videos">
              <div className="panel-header">
                <h2>任务详情</h2>
                <button onClick={upsertSchedule}>{selectedScheduleId ? '保存修改' : '保存任务'}</button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label className="form-field">
                  <span>任务名称</span>
                  <input
                    type="text"
                    value={scheduleForm.name}
                    onChange={(e) => setScheduleForm((p) => ({ ...p, name: e.target.value }))}
                  />
                </label>
                <label className="form-field">
                  <span>每日执行时间</span>
                  <input
                    type="time"
                    value={scheduleForm.time}
                    onChange={(e) => setScheduleForm((p) => ({ ...p, time: e.target.value }))}
                  />
                </label>
                <label className="form-field">
                  <span>每个博主每天最多拉取新视频</span>
                  <input
                    type="number"
                    min="1"
                    max="50"
                    value={scheduleForm.max_new_per_creator}
                    onChange={(e) =>
                      setScheduleForm((p) => ({ ...p, max_new_per_creator: Number(e.target.value || 0) }))
                    }
                  />
                </label>
                <label className="form-field">
                  <span>任务状态</span>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <input
                      type="checkbox"
                      checked={scheduleForm.enabled}
                      onChange={(e) => setScheduleForm((p) => ({ ...p, enabled: e.target.checked }))}
                    />
                    <span className="muted">{scheduleForm.enabled ? '启用' : '停用'}</span>
                  </div>
                </label>

                <label className="form-field">
                  <span>任务汇报邮箱（可选）</span>
                  <input
                    type="email"
                    value={scheduleForm.report_email}
                    placeholder="例如：me@example.com"
                    onChange={(e) => setScheduleForm((p) => ({ ...p, report_email: e.target.value }))}
                  />
                  <div className="muted" style={{ marginTop: 4 }}>
                    任务结束后会发送汇报邮件（需要后端配置 SMTP）。
                  </div>
                </label>

                <label className="form-field">
                  <span>生成定时任务总结（DeepSeek）</span>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <input
                      type="checkbox"
                      checked={scheduleForm.generate_summary}
                      onChange={(e) => setScheduleForm((p) => ({ ...p, generate_summary: e.target.checked }))}
                    />
                    <span className="muted">
                      勾选后会对本次新增分析的视频生成一份总结报告（供博主参考亮点/选题）。
                    </span>
                  </div>
                </label>

                <div className="form-field">
                  <span>目标博主（默认全选）</span>
                  <div className="muted" style={{ marginBottom: 6 }}>
                    默认全选。已选 {scheduleForm.creator_indices.length}/{creators.length}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {creators.map((c, idx) => {
                      const checked = scheduleForm.creator_indices.includes(idx)
                      return (
                        <label key={idx} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              const next = new Set(scheduleForm.creator_indices)
                              if (e.target.checked) next.add(idx)
                              else next.delete(idx)
                              setScheduleForm((p) => ({ ...p, creator_indices: Array.from(next).sort((a, b) => a - b) }))
                            }}
                          />
                          <span style={{ fontSize: 12, fontWeight: 600 }}>{c.name}</span>
                        </label>
                      )
                    })}
                  </div>
                </div>

                {selectedScheduleId && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>最近运行记录</div>
                    {loadingRuns && <div className="muted">加载中...</div>}
                    {!loadingRuns && scheduleRuns.length === 0 && <div className="muted">暂无运行记录</div>}
                    {!loadingRuns && scheduleRuns.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {scheduleRuns.slice(0, 10).map((r) => (
                          <div
                            key={r.id}
                            style={{
                              border: '1px solid #e5e7eb',
                              borderRadius: 10,
                              padding: '8px 10px',
                              background: '#fff',
                              fontSize: 12,
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                              <span style={{ fontWeight: 700 }}>{r.status}</span>
                              <span className="muted">run_id={r.id}</span>
                            </div>
                            {r.detail && <div className="muted" style={{ marginTop: 4 }}>{r.detail}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>
          </>
        )}
      </main>
      {activeResult && (
        <div
          className="modal-overlay"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setActiveResult(null)
          }}
        >
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <div className="modal-title">视频分析结果</div>
                <div className="modal-subtitle">{activeResult.title}</div>
              </div>
              <button
                className="btn btn-modal-close"
                onClick={() => setActiveResult(null)}
                aria-label="关闭结果弹窗"
              >
                关闭
              </button>
            </div>

            <div className="modal-body">
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeResult.markdown || ''}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}
      {activeReadable && (
        <div
          className="modal-overlay"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setActiveReadable(null)
          }}
        >
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <div className="modal-title">全文阅读</div>
                <div className="modal-subtitle">{activeReadable.title}</div>
                {activeReadable.cached && (
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    已缓存本地稿；若转录有更新可点「重新整理」。
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end' }}>
                <button
                  type="button"
                  className="btn btn-readable-outline"
                  disabled={loadingReadableId === activeReadable.aweme_id}
                  onClick={() =>
                    openReadable(
                      {
                        aweme_id: activeReadable.aweme_id,
                        title: activeReadable.title,
                        video_url: '',
                      },
                      true,
                    )
                  }
                >
                  {loadingReadableId === activeReadable.aweme_id ? '整理中...' : '重新整理'}
                </button>
                <button
                  type="button"
                  className="btn btn-modal-close"
                  onClick={() => setActiveReadable(null)}
                  aria-label="关闭全文阅读"
                >
                  关闭
                </button>
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
