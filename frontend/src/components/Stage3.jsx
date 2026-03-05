import ReactMarkdown from 'react-markdown';
import './Stage3.css';

export default function Stage3({ finalResponse }) {
  if (!finalResponse) {
    return null;
  }

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Assigned Execution + Final Output</h3>
      <div className="final-response">
        <div className="chairman-label">
          Coordinator: {finalResponse.model.split('/')[1] || finalResponse.model}
        </div>
        <div className="final-text markdown-content">
          <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
        </div>

        {finalResponse.execution_outputs && finalResponse.execution_outputs.length > 0 && (
          <div className="execution-outputs">
            <h4>Per-Model Execution Outputs</h4>
            {finalResponse.execution_outputs.map((output, index) => (
              <div key={`${output.model}-${index}`} className="execution-item">
                <div className="execution-label">
                  {output.model.split('/')[1] || output.model} — {output.responsibility}
                </div>
                <div className="execution-text markdown-content">
                  <ReactMarkdown>{output.output}</ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
