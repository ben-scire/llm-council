import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

export default function Stage2({ debates, metadata }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!debates || debates.length === 0) {
    return null;
  }

  const activeDebate = debates[activeTab] || {};
  const taskAssignments = metadata?.task_assignments || [];
  const consensusPlan = metadata?.consensus_plan || '';
  const roundsCompleted = metadata?.rounds_completed;
  const reachedInRounds = metadata?.consensus_reached_in_rounds;
  const fallbackUsed = metadata?.consensus_fallback_used;

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Multi-Model Debate and Consensus</h3>
      <p className="stage-description">
        All six models debate their independent plans, converge on one definitive plan,
        and assign responsibilities for execution.
      </p>

      <div className="aggregate-rankings">
        <h4>Consensus Snapshot</h4>
        <div className="aggregate-list">
          <div className="aggregate-item">
            <span className="rank-model">Rounds completed</span>
            <span className="rank-score">{roundsCompleted ?? 'N/A'}</span>
          </div>
          <div className="aggregate-item">
            <span className="rank-model">Consensus reached in debate rounds</span>
            <span className="rank-score">{reachedInRounds ? 'Yes' : 'No'}</span>
          </div>
          <div className="aggregate-item">
            <span className="rank-model">Coordinator fallback used</span>
            <span className="rank-score">{fallbackUsed ? 'Yes' : 'No'}</span>
          </div>
        </div>

        {consensusPlan && (
          <>
            <h4>Consensus Plan</h4>
            <div className="ranking-content markdown-content">
              <ReactMarkdown>{consensusPlan}</ReactMarkdown>
            </div>
          </>
        )}

        {taskAssignments.length > 0 && (
          <>
            <h4>Task Assignments</h4>
            <div className="parsed-ranking">
              <ol>
                {taskAssignments.map((assignment, i) => (
                  <li key={`${assignment.model}-${i}`}>
                    <strong>{assignment.model.split('/')[1] || assignment.model}</strong>: {assignment.responsibility}
                  </li>
                ))}
              </ol>
            </div>
          </>
        )}
      </div>

      <h4>Final-Round Debate Outputs</h4>
      <div className="tabs">
        {debates.map((debate, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {debate.model.split('/')[1] || debate.model}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          {activeDebate.model}
        </div>
        {activeDebate.consensus_status && (
          <p className="stage-description">
            This model's status: <strong>{activeDebate.consensus_status}</strong>
          </p>
        )}
        <div className="ranking-content markdown-content">
          <ReactMarkdown>{activeDebate.debate || activeDebate.ranking || ''}</ReactMarkdown>
        </div>

        {activeDebate.execution_notes && (
          <div className="parsed-ranking">
            <strong>Execution Notes:</strong>
            <div className="ranking-content markdown-content">
              <ReactMarkdown>{activeDebate.execution_notes}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
