import React from 'react';

interface StepBarProps {
  currentStep: number;
}

const STEPS = [
  { num: 1, label: 'Scan' },
  { num: 2, label: 'Review' },
  { num: 3, label: 'Confirm' },
  { num: 4, label: 'Execute' },
  { num: 5, label: 'Done' },
];

export function StepBar({ currentStep }: StepBarProps): React.JSX.Element {
  return (
    <div className="steps">
      {STEPS.map((step, i) => (
        <React.Fragment key={step.num}>
          {i > 0 && (
            <div className={`sl${step.num <= currentStep ? ' done' : ''}`} />
          )}
          <div
            className={`step${step.num === currentStep ? ' active' : ''}${step.num < currentStep ? ' done' : ''}`}
          >
            <div className="sn">
              {step.num < currentStep ? '\u2713' : step.num}
            </div>
            <span>{step.label}</span>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}
