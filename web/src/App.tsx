import React, { useEffect, useState, useCallback, useRef } from 'react';

export interface SettingsData {
  tg_api_id: string;
  tg_api_hash_configured: boolean;
  default_webhook_url: string;
  discord_global_enabled: boolean;
  max_media_mb: number;
  theme: 'mocha-peach' | 'latte-peach';
}

export interface TelegramAccount {
  id: number;
  first_name: string;
  last_name: string;
  username: string;
  phone: string;
}

export interface TelegramStatus {
  auth_state: 'unconfigured' | 'unauthorized' | 'awaiting_code' | 'awaiting_password' | 'authorized';
  account: TelegramAccount | null;
  pending_phone: string | null;
  enabled_group_count: number;
  last_error: string | null;
}

export interface GroupMapping {
  chat_id: number;
  chat_name: string;
  chat_type: string;
  username: string;
  enabled: boolean;
  discord_enabled: boolean;
  webhook_url: string;
  message_count: number;
  last_message_at: string | null;
  last_message_text: string;
  last_message_sender: string;
  last_message_media: string;
  updated_at: string;
}

export interface CapturedMessage {
  id: number;
  chat_id: number;
  chat_name: string;
  tg_message_id: number;
  sender_name: string;
  content: string;
  reply_to_msg_id: number | null;
  reply_author: string | null;
  reply_snippet: string | null;
  media_filename: string | null;
  media_path: string | null;
  media_url: string | null;
  media_mime: string | null;
  media_size: number;
  discord_status: 'sent' | 'paused' | 'failed' | 'archived' | 'no_webhook' | 'pending';
  discord_error: string | null;
  created_at: string;
}

function getInitials(name: string): string {
  const words = (name || '').trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return 'TG';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function formatShortTime(iso: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function formatBubbleTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function App() {
  const [loading, setLoading] = useState<boolean>(true);
  const [activeNavTab, setActiveNavTab] = useState<'chats' | 'settings'>('chats');
  const [hasResolvedInitialTab, setHasResolvedInitialTab] = useState<boolean>(false);

  const [toast, setToast] = useState<{ text: string; isError?: boolean } | null>(null);

  const [settings, setSettings] = useState<SettingsData>({
    tg_api_id: '',
    tg_api_hash_configured: false,
    default_webhook_url: '',
    discord_global_enabled: true,
    max_media_mb: 10,
    theme: 'mocha-peach',
  });

  const [telegram, setTelegram] = useState<TelegramStatus>({
    auth_state: 'unconfigured',
    account: null,
    pending_phone: null,
    enabled_group_count: 0,
    last_error: null,
  });

  const [groups, setGroups] = useState<GroupMapping[]>([]);
  const [messages, setMessages] = useState<CapturedMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState<boolean>(false);

  // Collapsible dropdown state for Checked vs Unchecked groups in sidebar
  const [checkedSectionOpen, setCheckedSectionOpen] = useState<boolean>(true);
  const [uncheckedSectionOpen, setUncheckedSectionOpen] = useState<boolean>(true);

  // Group search & chat selection
  const [groupSearch, setGroupSearch] = useState<string>('');
  const [selectedChatId, setSelectedChatId] = useState<number | null>(null);
  const [mobileShowChat, setMobileShowChat] = useState<boolean>(false);

  // Active Chat Header search
  const [chatSearchOpen, setChatSearchOpen] = useState<boolean>(false);
  const [messageSearch, setMessageSearch] = useState<string>('');

  // Busy flags
  const [syncingGroups, setSyncingGroups] = useState<boolean>(false);
  const [backfillingChatId, setBackfillingChatId] = useState<number | null>(null);
  const [testingWebhookKey, setTestingWebhookKey] = useState<string | null>(null);

  // Scraper Settings Tab Form State
  const [formApiId, setFormApiId] = useState<string>('');
  const [formApiHash, setFormApiHash] = useState<string>('');
  const [formWebhook, setFormWebhook] = useState<string>('');
  const [formMaxMediaMb, setFormMaxMediaMb] = useState<string>('10');
  const [formPhone, setFormPhone] = useState<string>('');
  const [formCode, setFormCode] = useState<string>('');
  const [formPassword, setFormPassword] = useState<string>('');
  const [savingSettings, setSavingSettings] = useState<boolean>(false);
  const [webhookDrafts, setWebhookDrafts] = useState<Record<number, string>>({});

  const streamEndRef = useRef<HTMLDivElement | null>(null);

  const showToast = useCallback((text: string, isError = false) => {
    setToast({ text, isError });
    window.setTimeout(() => {
      setToast((prev) => (prev?.text === text ? null : prev));
    }, 4000);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', settings.theme);
  }, [settings.theme]);

  const fetchInitialState = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch('/api/state');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSettings(data.settings);
      setTelegram(data.telegram);
      const loadedGroups: GroupMapping[] = data.groups || [];
      setGroups(loadedGroups);

      const drafts: Record<number, string> = {};
      for (const g of loadedGroups) {
        drafts[g.chat_id] = g.webhook_url || '';
      }
      setWebhookDrafts(drafts);

      setFormApiId(data.settings.tg_api_id || '');
      setFormWebhook(data.settings.default_webhook_url || '');
      setFormMaxMediaMb(String(data.settings.max_media_mb ?? 10));

      if (!hasResolvedInitialTab) {
        if (data.telegram.auth_state !== 'authorized') {
          setActiveNavTab('settings');
        } else {
          setActiveNavTab('chats');
          const firstChecked = loadedGroups.find((g) => g.enabled) || loadedGroups[0];
          if (firstChecked) {
            setSelectedChatId(firstChecked.chat_id);
          }
        }
        setHasResolvedInitialTab(true);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Could not load state: ${msg}`, true);
    } finally {
      setLoading(false);
    }
  }, [hasResolvedInitialTab, showToast]);

  const fetchMessagesForChat = useCallback(
    async (chatId: number | null, query: string) => {
      if (chatId === null) {
        setMessages([]);
        return;
      }
      setMessagesLoading(true);
      try {
        const params = new URLSearchParams();
        params.set('chat_id', String(chatId));
        if (query.trim()) {
          params.set('search', query.trim());
        }
        params.set('limit', '150');
        const resp = await fetch(`/api/messages?${params.toString()}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        setMessages(data.messages || []);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        showToast(`Failed to load messages: ${msg}`, true);
      } finally {
        setMessagesLoading(false);
      }
    },
    [showToast]
  );

  useEffect(() => {
    void fetchInitialState();
  }, [fetchInitialState]);

  useEffect(() => {
    void fetchMessagesForChat(selectedChatId, messageSearch);
  }, [selectedChatId, messageSearch, fetchMessagesForChat]);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, selectedChatId]);

  // Live SSE Stream
  useEffect(() => {
    const source = new EventSource('/api/stream');
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === 'new_message' && payload.message) {
          const incoming: CapturedMessage = payload.message;
          setMessages((prev) => {
            if (selectedChatId !== incoming.chat_id) return prev;
            const exists = prev.some((m) => m.id === incoming.id);
            if (exists) {
              return prev.map((m) => (m.id === incoming.id ? incoming : m));
            }
            return [...prev, incoming];
          });

          setGroups((prevGroups) =>
            prevGroups.map((g) =>
              g.chat_id === incoming.chat_id
                ? {
                    ...g,
                    message_count: g.message_count + 1,
                    last_message_at: incoming.created_at,
                    last_message_text: incoming.content || '',
                    last_message_sender: incoming.sender_name || '',
                    last_message_media: incoming.media_filename || '',
                  }
                : g
            )
          );
        } else if (payload.type === 'state_updated') {
          if (payload.settings) setSettings(payload.settings);
          if (payload.telegram) setTelegram(payload.telegram);
        }
      } catch {
        // Ignore malformed SSE event
      }
    };
    return () => source.close();
  }, [selectedChatId]);

  const handleToggleTheme = async (newTheme: 'mocha-peach' | 'latte-peach') => {
    setSettings((prev) => ({ ...prev, theme: newTheme }));
    try {
      await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: newTheme }),
      });
    } catch {
      // Keep local theme
    }
  };

  const handleToggleGlobalDiscord = async (enabled: boolean) => {
    setSettings((prev) => ({ ...prev, discord_global_enabled: enabled }));
    try {
      const resp = await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ discord_global_enabled: enabled }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSettings(data.settings);
      showToast(
        enabled
          ? 'Discord Webhook forwarding enabled.'
          : 'Discord Webhook forwarding paused.'
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Could not toggle Discord relay: ${msg}`, true);
    }
  };

  const handleUpdateGroup = async (
    chatId: number,
    patch: { enabled?: boolean; discord_enabled?: boolean; webhook_url?: string }
  ) => {
    try {
      const resp = await fetch(`/api/groups/${chatId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setGroups(data.groups || []);
      if (data.telegram) setTelegram(data.telegram);
      if (patch.webhook_url !== undefined) {
        showToast(`Saved custom webhook for ${data.group?.chat_name || chatId}.`);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Could not update group: ${msg}`, true);
    }
  };

  const handleSyncGroups = async () => {
    setSyncingGroups(true);
    try {
      const resp = await fetch('/api/groups/sync', { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      const synced: GroupMapping[] = data.groups || [];
      setGroups(synced);
      if (data.telegram) setTelegram(data.telegram);
      if (selectedChatId === null && synced.length > 0) {
        setSelectedChatId(synced[0].chat_id);
      }
      showToast(`Synced ${synced.length} groups from Telegram.`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Sync failed: ${msg}`, true);
    } finally {
      setSyncingGroups(false);
    }
  };

  const handleBackfillGroup = async (chatId: number) => {
    setBackfillingChatId(chatId);
    try {
      const resp = await fetch(`/api/groups/${chatId}/backfill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 25 }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      if (data.groups) setGroups(data.groups);
      if (selectedChatId === chatId) {
        setMessages(data.messages || []);
      } else {
        setSelectedChatId(chatId);
      }
      showToast(`Loaded ${data.captured_count} messages from Telegram.`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Could not load history: ${msg}`, true);
    } finally {
      setBackfillingChatId(null);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingSettings(true);
    try {
      const payload: Record<string, unknown> = {
        default_webhook_url: formWebhook.trim(),
        max_media_mb: parseFloat(formMaxMediaMb) || 10,
      };
      if (formApiId.trim()) payload.tg_api_id = formApiId.trim();
      if (formApiHash.trim()) payload.tg_api_hash = formApiHash.trim();

      const resp = await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setSettings(data.settings);
      setTelegram(data.telegram);
      setFormApiHash('');
      showToast('Settings saved to SQLite.');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Failed to save settings: ${msg}`, true);
    } finally {
      setSavingSettings(false);
    }
  };

  const handleTestWebhook = async (chatId?: number, customUrl?: string) => {
    const key = chatId ? String(chatId) : 'global';
    setTestingWebhookKey(key);
    try {
      const resp = await fetch('/api/webhook/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId ?? null, webhook_url: customUrl ?? null }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      showToast('Test message sent to Discord.');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Webhook test failed: ${msg}`, true);
    } finally {
      setTestingWebhookKey(null);
    }
  };

  const handleSendPhoneCode = async () => {
    setSavingSettings(true);
    try {
      const resp = await fetch('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: formPhone }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setTelegram(data.telegram);
      showToast(`Verification code sent to ${formPhone}.`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Failed to send code: ${msg}`, true);
    } finally {
      setSavingSettings(false);
    }
  };

  const handleVerifyOtpCode = async () => {
    setSavingSettings(true);
    try {
      const resp = await fetch('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: formCode, password: formPassword || undefined }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setTelegram(data.telegram);
      if (data.groups) setGroups(data.groups);
      if (data.telegram.auth_state === 'authorized') {
        showToast('Signed in to Telegram.');
        setActiveNavTab('chats');
        if (data.groups?.length > 0) {
          setSelectedChatId(data.groups[0].chat_id);
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Verification failed: ${msg}`, true);
    } finally {
      setSavingSettings(false);
    }
  };

  const handleVerify2FA = async () => {
    setSavingSettings(true);
    try {
      const resp = await fetch('/api/auth/verify-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: formPassword }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setTelegram(data.telegram);
      if (data.groups) setGroups(data.groups);
      if (data.telegram.auth_state === 'authorized') {
        showToast('Signed in to Telegram.');
        setActiveNavTab('chats');
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`2FA verification failed: ${msg}`, true);
    } finally {
      setSavingSettings(false);
    }
  };

  // Group filtering & separation into Checked and Unchecked
  const query = groupSearch.toLowerCase().trim();
  const filteredGroups = groups.filter((g) => {
    if (!query) return true;
    return (
      g.chat_name.toLowerCase().includes(query) ||
      g.username.toLowerCase().includes(query) ||
      String(g.chat_id).includes(query)
    );
  });

  const checkedList = filteredGroups.filter((g) => g.enabled);
  const uncheckedList = filteredGroups.filter((g) => !g.enabled);

  const activeGroup = groups.find((g) => g.chat_id === selectedChatId) || null;
  const allCheckedGroups = groups.filter((g) => g.enabled);

  const renderGroupItem = (grp: GroupMapping) => {
    const isSelected = selectedChatId === grp.chat_id;
    let previewLine = 'No messages archived yet';
    if (grp.last_message_text) {
      previewLine = grp.last_message_text;
    } else if (grp.last_message_media) {
      previewLine = `Attachment: ${grp.last_message_media}`;
    }

    return (
      <div
        key={grp.chat_id}
        role="listitem"
        className={`tg-dialog-item ${isSelected ? 'active' : ''}`}
        onClick={() => {
          setSelectedChatId(grp.chat_id);
          setMobileShowChat(true);
        }}
      >
        <label
          className="tg-check-hit"
          title={`Check to scrape ${grp.chat_name}`}
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            className="ui-checkbox"
            checked={grp.enabled}
            aria-label={`Scrape ${grp.chat_name}`}
            onChange={(e) =>
              handleUpdateGroup(grp.chat_id, { enabled: e.target.checked })
            }
          />
        </label>

        <div className="tg-avatar" aria-hidden="true">
          {getInitials(grp.chat_name)}
        </div>

        <div className="tg-dialog-body">
          <div className="tg-dialog-row1">
            <span className="tg-dialog-title">{grp.chat_name}</span>
            <span className="tg-dialog-time">
              {formatShortTime(grp.last_message_at)}
            </span>
          </div>

          <div className="tg-dialog-row2">
            <span className="tg-dialog-preview">
              {grp.last_message_sender ? (
                <>
                  <span className="tg-dialog-sender">
                    {grp.last_message_sender}:{' '}
                  </span>
                  {previewLine}
                </>
              ) : (
                previewLine
              )}
            </span>
            {grp.message_count > 0 && (
              <span className="tg-count-pill">{grp.message_count}</span>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="tg-app">
      {/* Far-Left Navigation Rail */}
      <nav className="tg-nav-rail" aria-label="Main navigation">
        <div className="tg-nav-group">
          <button
            type="button"
            className={`tg-rail-btn ${activeNavTab === 'chats' ? 'active' : ''}`}
            onClick={() => setActiveNavTab('chats')}
          >
            {telegram.enabled_group_count > 0 && (
              <span className="tg-rail-badge">{telegram.enabled_group_count}</span>
            )}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span>Chats</span>
          </button>

          <button
            type="button"
            className={`tg-rail-btn ${activeNavTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveNavTab('settings')}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <span>Settings</span>
          </button>
        </div>

        <div className="tg-nav-group">
          <button
            type="button"
            className="tg-rail-btn"
            title="Switch Catppuccin Theme"
            onClick={() =>
              handleToggleTheme(settings.theme === 'mocha-peach' ? 'latte-peach' : 'mocha-peach')
            }
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {settings.theme === 'mocha-peach' ? (
                <>
                  <circle cx="12" cy="12" r="5" />
                  <line x1="12" y1="1" x2="12" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="23" />
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                  <line x1="1" y1="12" x2="3" y2="12" />
                  <line x1="21" y1="12" x2="23" y2="12" />
                </>
              ) : (
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              )}
            </svg>
            <span>{settings.theme === 'mocha-peach' ? 'Mocha' : 'Latte'}</span>
          </button>
        </div>
      </nav>

      {/* VIEW 1: TELEGRAM CHATS SPLIT VIEW */}
      {activeNavTab === 'chats' && (
        <div className="tg-chats-workspace">
          {/* Left Group List Sidebar */}
          <aside className={`tg-sidebar ${mobileShowChat ? 'hidden-on-mobile' : ''}`}>
            <div className="tg-sidebar-header">
              <input
                type="search"
                className="ui-input"
                placeholder="Search groups..."
                aria-label="Search groups"
                value={groupSearch}
                onChange={(e) => setGroupSearch(e.target.value)}
              />
              <button
                type="button"
                className="ui-btn"
                title="Sync groups from Telegram"
                onClick={handleSyncGroups}
                disabled={syncingGroups || telegram.auth_state !== 'authorized'}
              >
                {syncingGroups ? 'Syncing...' : 'Sync'}
              </button>
            </div>

            <div className="tg-dialog-list" role="list">
              {loading ? (
                <div style={{ padding: '1.5rem 1rem', color: 'var(--text-secondary)', fontSize: '0.86rem' }}>
                  Loading groups...
                </div>
              ) : telegram.auth_state !== 'authorized' ? (
                <div style={{ padding: '1.5rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.86rem' }}>
                    Sign in to Telegram in Settings to load your joined groups.
                  </p>
                  <button
                    type="button"
                    className="ui-btn ui-btn-primary"
                    onClick={() => setActiveNavTab('settings')}
                  >
                    Open Settings
                  </button>
                </div>
              ) : filteredGroups.length === 0 ? (
                <div style={{ padding: '1.5rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.86rem' }}>
                    {groups.length === 0
                      ? 'No groups loaded yet.'
                      : 'No groups match your search.'}
                  </p>
                  {groups.length === 0 && (
                    <button type="button" className="ui-btn ui-btn-primary" onClick={handleSyncGroups}>
                      Sync Groups from Telegram
                    </button>
                  )}
                </div>
              ) : (
                <>
                  {/* Checked Groups Collapsible Section */}
                  <div className="tg-section-group">
                    <button
                      type="button"
                      className="tg-section-toggle"
                      onClick={() => setCheckedSectionOpen((prev) => !prev)}
                      aria-expanded={checkedSectionOpen}
                    >
                      <span className="tg-section-toggle-left">
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          className={`tg-chevron ${!checkedSectionOpen ? 'collapsed' : ''}`}
                        >
                          <polyline points="6 9 12 15 18 9" />
                        </svg>
                        <span>Checked Groups ({checkedList.length})</span>
                      </span>
                    </button>

                    {checkedSectionOpen && (
                      checkedList.length === 0 ? (
                        <div style={{ padding: '0.5rem 0.6rem 0.8rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                          No groups checked yet. Check any box below to enable live scraping.
                        </div>
                      ) : (
                        checkedList.map(renderGroupItem)
                      )
                    )}
                  </div>

                  {/* Unchecked Groups Collapsible Section */}
                  <div className="tg-section-group" style={{ marginTop: '0.35rem' }}>
                    <button
                      type="button"
                      className="tg-section-toggle"
                      onClick={() => setUncheckedSectionOpen((prev) => !prev)}
                      aria-expanded={uncheckedSectionOpen}
                    >
                      <span className="tg-section-toggle-left">
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          className={`tg-chevron ${!uncheckedSectionOpen ? 'collapsed' : ''}`}
                        >
                          <polyline points="6 9 12 15 18 9" />
                        </svg>
                        <span>Other Groups ({uncheckedList.length})</span>
                      </span>
                    </button>

                    {uncheckedSectionOpen && (
                      uncheckedList.length === 0 ? (
                        <div style={{ padding: '0.5rem 0.6rem 0.8rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                          All groups are currently checked.
                        </div>
                      ) : (
                        uncheckedList.map(renderGroupItem)
                      )
                    )}
                  </div>
                </>
              )}
            </div>
          </aside>

          {/* Right Chat Stream Pane */}
          <section className={`tg-chat-pane ${!mobileShowChat ? 'hidden-on-mobile' : ''}`}>
            {!activeGroup ? (
              <div className="tg-empty-chat">
                <span className="tg-empty-pill">Select a group to view archived chats</span>
              </div>
            ) : (
              <>
                {/* Header */}
                <header className="tg-chat-header">
                  <div className="tg-chat-header-info">
                    <button
                      type="button"
                      className="ui-btn mobile-back-btn"
                      onClick={() => setMobileShowChat(false)}
                    >
                      Back
                    </button>
                    <div className="tg-avatar" style={{ width: 36, height: 36, fontSize: '0.82rem' }}>
                      {getInitials(activeGroup.chat_name)}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <h1 className="tg-chat-header-title">{activeGroup.chat_name}</h1>
                      <div className="tg-chat-header-sub">
                        {activeGroup.enabled ? 'Scraping active' : 'Scraping inactive'} •{' '}
                        {activeGroup.message_count} messages
                      </div>
                    </div>
                  </div>

                  <div className="tg-chat-header-actions">
                    {chatSearchOpen && (
                      <input
                        type="search"
                        className="ui-input"
                        style={{ width: 190 }}
                        placeholder="Search messages..."
                        value={messageSearch}
                        onChange={(e) => setMessageSearch(e.target.value)}
                        autoFocus
                      />
                    )}
                    <button
                      type="button"
                      className="ui-btn"
                      onClick={() => {
                        if (chatSearchOpen) setMessageSearch('');
                        setChatSearchOpen((prev) => !prev);
                      }}
                    >
                      {chatSearchOpen ? 'Close' : 'Search'}
                    </button>

                    <button
                      type="button"
                      className="ui-btn"
                      disabled={backfillingChatId === activeGroup.chat_id}
                      onClick={() => handleBackfillGroup(activeGroup.chat_id)}
                    >
                      {backfillingChatId === activeGroup.chat_id ? 'Loading...' : 'Load History'}
                    </button>
                  </div>
                </header>

                {/* Message Bubbles Stream */}
                <div className="tg-message-stream">
                  {messagesLoading && messages.length === 0 ? (
                    <div className="tg-empty-chat">
                      <span className="tg-empty-pill">Loading messages...</span>
                    </div>
                  ) : messages.length === 0 ? (
                    <div className="tg-empty-chat">
                      <span className="tg-empty-pill">No messages archived in this group yet</span>
                      <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.84rem' }}>
                        {activeGroup.enabled
                          ? 'New incoming messages in this group will appear here live, or you can load recent history from Telegram.'
                          : 'Check the box next to this group in the sidebar to enable live scraping, or load recent history below.'}
                      </p>
                      <button
                        type="button"
                        className="ui-btn ui-btn-primary"
                        disabled={backfillingChatId === activeGroup.chat_id}
                        onClick={() => handleBackfillGroup(activeGroup.chat_id)}
                      >
                        {backfillingChatId === activeGroup.chat_id
                          ? 'Loading Recent Messages...'
                          : 'Load Recent 25 Messages'}
                      </button>
                    </div>
                  ) : (
                    <div className="tg-message-stream-inner">
                      {messages.map((msg) => {
                        const isImage =
                          msg.media_mime?.startsWith('image/') ||
                          /\.(jpg|jpeg|png|gif|webp)$/i.test(msg.media_filename || '');
                        const isVideo =
                          msg.media_mime?.startsWith('video/') ||
                          /\.(mp4|webm|mov)$/i.test(msg.media_filename || '');
                        const isAudio =
                          msg.media_mime?.startsWith('audio/') ||
                          /\.(ogg|mp3|wav|m4a)$/i.test(msg.media_filename || '');

                        return (
                          <div key={msg.id} className="tg-msg-row">
                            <div className="tg-msg-avatar" aria-hidden="true">
                              {getInitials(msg.sender_name)}
                            </div>

                            <div className="tg-bubble">
                              <div className="tg-bubble-sender">{msg.sender_name}</div>

                              {msg.reply_author && (
                                <div className="tg-reply-bar">
                                  <span className="tg-reply-author">{msg.reply_author}</span>
                                  <span className="tg-reply-snippet">
                                    {msg.reply_snippet || 'Attachment'}
                                  </span>
                                </div>
                              )}

                              {msg.media_url && (
                                <div className="tg-media-box">
                                  {isImage ? (
                                    <a href={msg.media_url} target="_blank" rel="noreferrer">
                                      <img
                                        src={msg.media_url}
                                        alt={msg.media_filename || 'Photo'}
                                        className="tg-media-img"
                                        loading="lazy"
                                      />
                                    </a>
                                  ) : isVideo ? (
                                    <video controls className="tg-media-video" preload="metadata">
                                      <source
                                        src={msg.media_url}
                                        type={msg.media_mime || 'video/mp4'}
                                      />
                                    </video>
                                  ) : isAudio ? (
                                    <audio controls className="tg-media-audio" preload="metadata">
                                      <source
                                        src={msg.media_url}
                                        type={msg.media_mime || 'audio/ogg'}
                                      />
                                    </audio>
                                  ) : (
                                    <a
                                      href={msg.media_url}
                                      download={msg.media_filename || true}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="tg-file-attachment"
                                    >
                                      <span>{msg.media_filename || 'Attachment'}</span>
                                      {msg.media_size > 0 && (
                                        <span style={{ color: 'var(--text-muted)', fontSize: '0.74rem' }}>
                                          ({formatBytes(msg.media_size)})
                                        </span>
                                      )}
                                    </a>
                                  )}
                                </div>
                              )}

                              {msg.content && <p className="tg-bubble-text">{msg.content}</p>}

                              <span className="tg-bubble-meta">
                                {formatBubbleTime(msg.created_at)}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                      <div ref={streamEndRef} />
                    </div>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {/* VIEW 2: DEDICATED SCRAPER & DISCORD SETTINGS TAB */}
      {activeNavTab === 'settings' && (
        <main className="tg-settings-view">
          <div className="tg-settings-container">
            <div>
              <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700 }}>
                Scraper Settings
              </h1>
              <p style={{ margin: '0.2rem 0 0', color: 'var(--text-secondary)', fontSize: '0.84rem' }}>
                Manage Discord Webhook forwarding, per-group routing, and Telegram session credentials.
              </p>
            </div>

            {/* Card 1: Discord Webhook Global Configuration */}
            <section className="tg-settings-card">
              <div className="tg-card-header">
                <div>
                  <h2>Discord Forwarding</h2>
                  <p className="tg-card-sub">
                    Forward incoming messages and attachments from checked Telegram groups to Discord.
                  </p>
                </div>
                <label className="ui-checkbox-label">
                  <input
                    type="checkbox"
                    className="ui-checkbox"
                    checked={settings.discord_global_enabled}
                    onChange={(e) => handleToggleGlobalDiscord(e.target.checked)}
                  />
                  <span>Forward to Discord</span>
                </label>
              </div>

              <form onSubmit={handleSaveSettings} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                <div className="tg-form-grid">
                  <div className="tg-field">
                    <label htmlFor="webhook-url-input" className="tg-label">
                      Default Discord Webhook URL
                    </label>
                    <div className="tg-input-group">
                      <input
                        id="webhook-url-input"
                        type="url"
                        className="ui-input mono"
                        placeholder="https://discord.com/api/webhooks/..."
                        value={formWebhook}
                        onChange={(e) => setFormWebhook(e.target.value)}
                      />
                      <button
                        type="button"
                        className="ui-btn"
                        disabled={testingWebhookKey === 'global' || !formWebhook.trim()}
                        onClick={() => handleTestWebhook(undefined, formWebhook)}
                      >
                        {testingWebhookKey === 'global' ? 'Testing...' : 'Test'}
                      </button>
                    </div>
                  </div>

                  <div className="tg-field">
                    <label htmlFor="max-media-mb-input" className="tg-label">
                      Max File Size (MB)
                    </label>
                    <input
                      id="max-media-mb-input"
                      type="number"
                      step="0.5"
                      min="1"
                      max="500"
                      className="ui-input mono"
                      value={formMaxMediaMb}
                      onChange={(e) => setFormMaxMediaMb(e.target.value)}
                    />
                  </div>
                </div>

                <div className="tg-card-footer">
                  <button type="submit" className="ui-btn ui-btn-primary" disabled={savingSettings}>
                    {savingSettings ? 'Saving...' : 'Save Webhook Settings'}
                  </button>
                </div>
              </form>
            </section>

            {/* Card 2: Checked Groups & Per-Group Routing */}
            <section className="tg-settings-card">
              <div className="tg-card-header">
                <div>
                  <h2>Checked Groups Routing ({allCheckedGroups.length})</h2>
                  <p className="tg-card-sub">
                    Groups enabled for scraping. By default each uses the Default Webhook URL above, or you can provide a custom webhook override.
                  </p>
                </div>
              </div>

              {allCheckedGroups.length === 0 ? (
                <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: '0.84rem' }}>
                  No groups are checked yet. Go to the Chats tab and check the box next to any group.
                </p>
              ) : (
                <div className="tg-route-list">
                  {allCheckedGroups.map((grp) => {
                    const draftUrl = webhookDrafts[grp.chat_id] ?? grp.webhook_url;
                    return (
                      <div key={grp.chat_id} className="tg-route-row">
                        <div className="tg-route-group-info">
                          <label
                            className="tg-check-hit"
                            title="Uncheck to disable scraping"
                          >
                            <input
                              type="checkbox"
                              className="ui-checkbox"
                              checked={grp.enabled}
                              onChange={(e) =>
                                handleUpdateGroup(grp.chat_id, { enabled: e.target.checked })
                              }
                            />
                          </label>
                          <div style={{ minWidth: 0 }}>
                            <div className="tg-route-group-title">{grp.chat_name}</div>
                            <div className="tg-route-group-id mono">{grp.chat_id}</div>
                          </div>
                        </div>

                        <div className="tg-route-controls">
                          <input
                            type="url"
                            className="ui-input mono"
                            placeholder="Custom Discord Webhook URL (leave empty for Default)"
                            aria-label={`Custom Webhook for ${grp.chat_name}`}
                            value={draftUrl}
                            onChange={(e) =>
                              setWebhookDrafts((prev) => ({
                                ...prev,
                                [grp.chat_id]: e.target.value,
                              }))
                            }
                          />
                          <button
                            type="button"
                            className="ui-btn"
                            onClick={() =>
                              handleUpdateGroup(grp.chat_id, { webhook_url: draftUrl })
                            }
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="ui-btn"
                            disabled={testingWebhookKey === String(grp.chat_id)}
                            onClick={() => handleTestWebhook(grp.chat_id, draftUrl)}
                          >
                            Test
                          </button>
                          <label className="ui-checkbox-label" style={{ marginLeft: '0.25rem', whiteSpace: 'nowrap' }}>
                            <input
                              type="checkbox"
                              className="ui-checkbox"
                              checked={grp.discord_enabled}
                              onChange={(e) =>
                                handleUpdateGroup(grp.chat_id, { discord_enabled: e.target.checked })
                              }
                            />
                            <span>Relay</span>
                          </label>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            {/* Card 3: Telegram Account & API Credentials */}
            <section className="tg-settings-card">
              <div className="tg-card-header">
                <div>
                  <h2>Telegram Account & Credentials</h2>
                  <p className="tg-card-sub">
                    {telegram.auth_state === 'authorized' && telegram.account
                      ? `Signed in as ${telegram.account.first_name} ${
                          telegram.account.username ? `(@${telegram.account.username})` : ''
                        }`
                      : 'Configure API credentials from my.telegram.org and sign in via phone number.'}
                  </p>
                </div>
              </div>

              <form onSubmit={handleSaveSettings} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
                  <div className="tg-field">
                    <label htmlFor="tg-api-id" className="tg-label">
                      Telegram API ID
                    </label>
                    <input
                      id="tg-api-id"
                      type="text"
                      className="ui-input mono"
                      placeholder="12345678"
                      value={formApiId}
                      onChange={(e) => setFormApiId(e.target.value)}
                    />
                  </div>

                  <div className="tg-field">
                    <label htmlFor="tg-api-hash" className="tg-label">
                      Telegram API Hash {settings.tg_api_hash_configured ? '(Saved)' : ''}
                    </label>
                    <input
                      id="tg-api-hash"
                      type="password"
                      className="ui-input mono"
                      placeholder={
                        settings.tg_api_hash_configured
                          ? '••••••••••••••••••••••••'
                          : 'Enter 32-character API Hash'
                      }
                      value={formApiHash}
                      onChange={(e) => setFormApiHash(e.target.value)}
                    />
                  </div>
                </div>

                <div className="tg-card-footer">
                  <button type="submit" className="ui-btn" disabled={savingSettings}>
                    Save API Credentials
                  </button>
                </div>
              </form>

              {telegram.auth_state !== 'authorized' && (
                <div style={{ paddingTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem', borderTop: '1px solid var(--divider)' }}>
                  {telegram.auth_state === 'unauthorized' && (
                    <div className="tg-field">
                      <label htmlFor="phone-login" className="tg-label">
                        Phone Number (International Format)
                      </label>
                      <div className="tg-input-group">
                        <input
                          id="phone-login"
                          type="tel"
                          className="ui-input mono"
                          placeholder="+6281234567890"
                          value={formPhone}
                          onChange={(e) => setFormPhone(e.target.value)}
                        />
                        <button
                          type="button"
                          className="ui-btn ui-btn-primary"
                          disabled={savingSettings || !formPhone.trim()}
                          onClick={handleSendPhoneCode}
                        >
                          Send Login Code
                        </button>
                      </div>
                    </div>
                  )}

                  {telegram.auth_state === 'awaiting_code' && (
                    <div className="tg-field">
                      <label htmlFor="otp-code" className="tg-label">
                        5-Digit Code sent to {telegram.pending_phone}
                      </label>
                      <div className="tg-input-group">
                        <input
                          id="otp-code"
                          type="text"
                          className="ui-input mono"
                          placeholder="12345"
                          value={formCode}
                          onChange={(e) => setFormCode(e.target.value)}
                        />
                        <button
                          type="button"
                          className="ui-btn ui-btn-primary"
                          disabled={savingSettings || !formCode.trim()}
                          onClick={handleVerifyOtpCode}
                        >
                          Verify Code
                        </button>
                      </div>
                    </div>
                  )}

                  {telegram.auth_state === 'awaiting_password' && (
                    <div className="tg-field">
                      <label htmlFor="twofa-pass" className="tg-label">
                        Telegram 2FA Password
                      </label>
                      <div className="tg-input-group">
                        <input
                          id="twofa-pass"
                          type="password"
                          className="ui-input"
                          placeholder="Enter 2FA password"
                          value={formPassword}
                          onChange={(e) => setFormPassword(e.target.value)}
                        />
                        <button
                          type="button"
                          className="ui-btn ui-btn-primary"
                          disabled={savingSettings || !formPassword}
                          onClick={handleVerify2FA}
                        >
                          Sign In
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </section>
          </div>
        </main>
      )}

      {toast && (
        <div className={`tg-toast ${toast.isError ? 'error' : ''}`} role="status">
          <span>{toast.text}</span>
        </div>
      )}
    </div>
  );
}
