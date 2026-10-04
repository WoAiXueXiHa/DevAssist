import axios from "axios";

const api = axios.create({ baseURL: "/api" });

// 每个请求自动带上本地存储的 JWT
api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

export interface UserInfo {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  tenant_id: string;
  tenant_name?: string;
}

export const isInternal = (role: string) => role === "support" || role === "admin";

export async function login(username: string, password: string) {
  const { data } = await api.post("/auth/login", { username, password });
  localStorage.setItem("token", data.access_token);
  localStorage.setItem("user", JSON.stringify(data.user));
  return data.user as UserInfo;
}

export function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}

export function currentUser(): UserInfo | null {
  const raw = localStorage.getItem("user");
  return raw ? JSON.parse(raw) : null;
}

export const listConversations = () => api.get("/conversations").then((r) => r.data);
export const getConversation = (id: string) => api.get(`/conversations/${id}`).then((r) => r.data);
export const sendCustomerMessage = (id: string, content: string) => api.post(`/conversations/${id}/messages`, { content }).then((r) => r.data);
export const myTickets = () => api.get("/tickets").then((r) => r.data);
export const submitFeedback = (body: any) => api.post("/feedback", body).then((r) => r.data);
export const wbTickets = (params: any) => api.get("/workbench/tickets", { params }).then((r) => r.data);
export const wbTicketDetail = (id: string) => api.get(`/workbench/tickets/${id}`).then((r) => r.data);
export const wbUpdateTicket = (id: string, body: any) => api.post(`/workbench/tickets/${id}`, body).then((r) => r.data);
export const wbSuggestReply = (convId: string) => api.get(`/workbench/conversations/${convId}/suggest_reply`).then((r) => r.data);
export const wbReply = (convId: string, content: string) => api.post(`/workbench/conversations/${convId}/reply`, { content }).then((r) => r.data);
export const listDocs = () => api.get("/docs").then((r) => r.data);
export const getDoc = (id: string) => api.get(`/docs/${id}`).then((r) => r.data);
export const getTrace = (id: string) => api.get(`/traces/${id}`).then((r) => r.data);
export const getMetrics = () => api.get("/metrics").then((r) => r.data);

/** SSE over fetch（后端 /api/chat 为 POST + EventSource 不支持 POST，故手动解析）。*/
export async function chatStream(
  message: string,
  conversationId: string | null,
  handlers: {
    onMeta?: (m: any) => void;
    onToken?: (t: string) => void;
    onDone?: (d: any) => void;
  }
) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (!resp.ok) {
    const errorBody = await resp.json().catch(() => null);
    const detail = errorBody?.detail;
    throw new Error(typeof detail === "string" ? detail : `请求失败（${resp.status}）`);
  }
  if (!resp.body || !resp.headers.get("content-type")?.includes("text/event-stream")) {
    throw new Error("对话服务返回格式异常");
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";
  let dataLines: string[] = [];
  let receivedDone = false;
  // 在空行处派发完整事件，支持 CRLF、多行 data 与跨网络块的中文字符。
  const line = (raw: string) => {
    const text = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (text === "") {
      if (dataLines.length) {
        const data = dataLines.join("\n");
        if (event === "meta") handlers.onMeta?.(JSON.parse(data));
        else if (event === "token") handlers.onToken?.(data);
        else if (event === "done") {
          const result = JSON.parse(data);
          receivedDone = true;
          handlers.onDone?.(result);
        } else if (event === "error") throw new Error(data || "对话中断");
      }
      event = "";
      dataLines = [];
    } else if (text.startsWith("event:")) event = text.slice(6).trim();
    else if (text.startsWith("data:")) dataLines.push(text.slice(5).replace(/^ /, ""));
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buf += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const item of lines) line(item);
      if (done) break;
    }
    // EOF 只代表连接结束；缺少 done 不能当作一次成功回复。
    if (!receivedDone) throw new Error("对话连接提前结束，请查看会话历史后重试");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export default api;
