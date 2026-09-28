import { useEffect, useMemo, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { X, Terminal as TermIcon } from 'lucide-react'
import { api, terminalSocketUrl } from '../api'
import type { WorkView } from '../api'
import { workStatusSemantics } from '../work-status.generated'
import { StatusBadge } from './StatusBadge'

interface TerminalViewProps {
  terminalId: string
  provider?: string
  agentProfile?: string | null
  /** Explicit durable work identity. Never inferred from terminal metadata. */
  workItemId?: string
  /** Terminal liveness/presentation, independent from any durable work state. */
  terminalStatus?: string | null
  onClose: () => void
}

type ManualWorkSelection = {
  terminalId: string
  propWorkItemId: string | undefined
  inputValue: string
  selectedWorkItemId: string | null
  validationError: string | null
  requestVersion: number
}

type WorkObservation = {
  terminalId: string
  workItemId: string
  state: 'loading' | 'loaded' | 'unavailable'
  workView?: WorkView
}

type AcceptedWorkObservation = {
  terminalId: string
  workItemId: string
  revision: number
  workView: WorkView
  invalidated: boolean
}

function isWorkViewForSelection(value: unknown, expectedId: string): value is WorkView {
  if (!value || typeof value !== 'object') return false
  const view = value as Partial<WorkView>
  return view.schema_version === 1
    && view.work_item_id === expectedId
    && typeof view.work_state === 'string'
    && workStatusSemantics(view.work_state) !== undefined
    && typeof view.revision === 'number'
    && Number.isSafeInteger(view.revision)
    && view.revision >= 0
}

export function TerminalView({ terminalId, provider, agentProfile, workItemId, terminalStatus, onClose }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [manualSelection, setManualSelection] = useState<ManualWorkSelection>(() => ({
    terminalId,
    propWorkItemId: workItemId,
    inputValue: '',
    selectedWorkItemId: null,
    validationError: null,
    requestVersion: 0,
  }))
  const manualSelectionIsCurrent = manualSelection.terminalId === terminalId
    && manualSelection.propWorkItemId === workItemId
  const currentManualSelection = manualSelectionIsCurrent
    ? manualSelection
    : {
        terminalId,
        propWorkItemId: workItemId,
        inputValue: '',
        selectedWorkItemId: null,
        validationError: null,
        requestVersion: 0,
      }

  const hasPropSelection = workItemId !== undefined
  const selectedWorkItemId = hasPropSelection ? workItemId : currentManualSelection.selectedWorkItemId
  const propRequestKey = useMemo(() => ({}), [terminalId, workItemId])
  const requestKey: object | number = hasPropSelection ? propRequestKey : currentManualSelection.requestVersion

  const [workObservation, setWorkObservation] = useState<WorkObservation | null>(null)
  const acceptedWorkObservation = useRef<AcceptedWorkObservation | null>(null)

  useEffect(() => {
    if (manualSelectionIsCurrent) return
    setManualSelection({
      terminalId,
      propWorkItemId: workItemId,
      inputValue: '',
      selectedWorkItemId: null,
      validationError: null,
      requestVersion: 0,
    })
  }, [terminalId, workItemId, manualSelection.terminalId, manualSelection.propWorkItemId, manualSelectionIsCurrent])

  useEffect(() => {
    let current = true
    if (selectedWorkItemId === null) {
      setWorkObservation(null)
      acceptedWorkObservation.current = null
      return () => {
        current = false
      }
    }

    const previousAccepted = acceptedWorkObservation.current
    if (!previousAccepted
      || previousAccepted.terminalId !== terminalId
      || previousAccepted.workItemId !== selectedWorkItemId) {
      acceptedWorkObservation.current = null
      setWorkObservation({ terminalId, workItemId: selectedWorkItemId, state: 'loading' })
    }

    void api.getWorkItem(selectedWorkItemId)
      .then((view: unknown) => {
        if (!current) return
        if (isWorkViewForSelection(view, selectedWorkItemId)) {
          const accepted = acceptedWorkObservation.current
          if (accepted
            && accepted.terminalId === terminalId
            && accepted.workItemId === selectedWorkItemId) {
            if (view.revision < accepted.revision) return
            if (view.revision === accepted.revision
              && (accepted.invalidated || view.work_state !== accepted.workView.work_state)) {
              accepted.invalidated = true
              setWorkObservation({ terminalId, workItemId: selectedWorkItemId, state: 'unavailable' })
              return
            }
          }

          acceptedWorkObservation.current = {
            terminalId,
            workItemId: selectedWorkItemId,
            revision: view.revision,
            workView: view,
            invalidated: false,
          }
          setWorkObservation({ terminalId, workItemId: selectedWorkItemId, state: 'loaded', workView: view })
        } else {
          setWorkObservation({ terminalId, workItemId: selectedWorkItemId, state: 'unavailable' })
        }
      })
      .catch(() => {
        if (current) setWorkObservation({ terminalId, workItemId: selectedWorkItemId, state: 'unavailable' })
      })

    return () => {
      current = false
    }
  }, [terminalId, selectedWorkItemId, requestKey])

  const visibleObservation = workObservation?.terminalId === terminalId
    && workObservation.workItemId === selectedWorkItemId
    ? workObservation
    : null
  const observedSemantics = visibleObservation?.state === 'loaded' && visibleObservation.workView
    ? workStatusSemantics(visibleObservation.workView.work_state)
    : undefined

  const handleObserveWork = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (hasPropSelection) return

    const normalizedId = currentManualSelection.inputValue.trim()
    if (!normalizedId) {
      setManualSelection({ ...currentManualSelection, validationError: 'Enter a work item ID' })
      return
    }

    setManualSelection({
      ...currentManualSelection,
      selectedWorkItemId: normalizedId,
      validationError: null,
      requestVersion: currentManualSelection.requestVersion + 1,
    })
  }

  const handleClearObservedWork = () => {
    if (hasPropSelection) return
    setManualSelection({
      ...currentManualSelection,
      inputValue: '',
      selectedWorkItemId: null,
      validationError: null,
      requestVersion: currentManualSelection.requestVersion + 1,
    })
  }

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
      scrollback: 10000,
      theme: {
        background: '#0d1117',
        foreground: '#c9d1d9',
        cursor: '#58a6ff',
        selectionBackground: '#264f78',
        black: '#0d1117',
        red: '#ff7b72',
        green: '#3fb950',
        yellow: '#d29922',
        blue: '#58a6ff',
        magenta: '#bc8cff',
        cyan: '#39d353',
        white: '#c9d1d9',
      },
    })

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(el)

    // Connect WebSocket
    const ws = new WebSocket(terminalSocketUrl(terminalId))
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      // Fit once the connection is live so we send correct dimensions
      fitAddon.fit()
      ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
    }

    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(e.data))
      }
    }

    ws.onclose = () => {
      term.write('\r\n\x1b[33m[Connection closed]\x1b[0m\r\n')
    }

    // Copy selection to clipboard on mouse-up
    term.onSelectionChange(() => {
      const selection = term.getSelection()
      if (selection) {
        navigator.clipboard.writeText(selection).catch(() => {})
      }
    })

    // Ctrl+Shift+C to copy selection
    term.attachCustomKeyEventHandler((e) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'C') {
        const selection = term.getSelection()
        if (selection) navigator.clipboard.writeText(selection).catch(() => {})
        return false
      }
      return true
    })

    // onData handles ALL input including paste — xterm.js
    // receives pasted text through the browser's input system
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    // Handle resize — debounce to avoid flooding
    let resizeTimer: ReturnType<typeof setTimeout>
    const resizeObserver = new ResizeObserver(() => {
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        fitAddon.fit()
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
        }
      }, 50)
    })
    resizeObserver.observe(el)

    // Initial fit after layout settles
    const initialFit = requestAnimationFrame(() => {
      fitAddon.fit()
    })

    term.focus()

    return () => {
      cancelAnimationFrame(initialFit)
      clearTimeout(resizeTimer)
      resizeObserver.disconnect()
      ws.close()
      term.dispose()
    }
  }, [terminalId])

  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: '#0d1117' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0">
        <div className="flex items-center gap-3">
          <TermIcon size={16} className="text-emerald-400" />
          <span className="text-sm font-mono text-gray-300">{terminalId}</span>
          {provider && <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">{provider}</span>}
          {agentProfile && <span className="text-xs text-emerald-400 bg-emerald-900/30 px-2 py-0.5 rounded">{agentProfile}</span>}
          {terminalStatus != null && (
            <span className="inline-flex items-center gap-2">
              {selectedWorkItemId && <span className="text-xs text-gray-300">Terminal:</span>}
              <StatusBadge status={terminalStatus} />
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-gray-600">Click X to close</span>
          <button
            onClick={onClose}
            className="p-1 text-gray-500 hover:text-white transition-colors rounded"
            title="Close terminal"
          >
            <X size={18} />
          </button>
        </div>
      </div>
      <section
        aria-label={selectedWorkItemId === null ? 'Observed work' : `Observed work: ${selectedWorkItemId}`}
        className="flex items-center gap-3 px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0 text-xs text-gray-300"
      >
        {selectedWorkItemId === null ? (
          <span>No work selected</span>
        ) : (
          <>
            <span>Observed work: {selectedWorkItemId}</span>
            <StatusBadge
              status={null}
              workView={visibleObservation?.state === 'loaded' ? visibleObservation.workView : null}
            />
            <span aria-live="polite">
              {visibleObservation?.state === 'loading' && 'Loading observed work'}
              {visibleObservation?.state === 'unavailable' && 'Work unavailable'}
            </span>
            <span>Observed running: {observedSemantics?.observedRunningCount ?? '—'}</span>
            <span>Observed succeeded: {observedSemantics?.observedSucceededCount ?? '—'}</span>
          </>
        )}
      </section>
      {!hasPropSelection && (
        <form onSubmit={handleObserveWork} className="flex items-center gap-2 px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0">
          <label htmlFor="work-item-id" className="text-xs text-gray-300">Work item ID</label>
          <input
            id="work-item-id"
            type="text"
            value={currentManualSelection.inputValue}
            onChange={(event) => setManualSelection({
              ...currentManualSelection,
              inputValue: event.currentTarget.value,
              validationError: null,
            })}
            className="min-w-0 px-2 py-1 text-sm font-mono text-gray-200 bg-gray-800 border border-gray-700 rounded"
          />
          <button type="submit" className="px-2 py-1 text-xs text-gray-200 bg-gray-700 rounded hover:bg-gray-600">
            Observe work
          </button>
          {selectedWorkItemId && (
            <button type="button" onClick={handleClearObservedWork} className="px-2 py-1 text-xs text-gray-300 bg-gray-800 rounded hover:bg-gray-700">
              Clear observed work
            </button>
          )}
        </form>
      )}
      {currentManualSelection.validationError && <div aria-live="polite" className="px-4 py-1 text-xs text-gray-300 bg-gray-900 shrink-0">{currentManualSelection.validationError}</div>}
      {/* Terminal — absolute positioning gives xterm.js real pixel dimensions to measure */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <div ref={containerRef} style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} />
      </div>
    </div>
  )
}
