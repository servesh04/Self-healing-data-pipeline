import { postRunChat } from '../../api/client'
import ChatPanel from './ChatPanel'

const SUGGESTED = ['Why did this escalate?', 'What did the specialist propose?', 'Why was confidence low?']

export default function RunChat({ runId }) {
  return (
    <ChatPanel
      suggested={SUGGESTED}
      placeholder="Ask about this run…"
      onSend={(messages) => postRunChat(runId, messages)}
    />
  )
}
