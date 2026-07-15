import React from 'react';

interface StepBarProps {
  currentStep: number;
}

const STEPS = [
  { num: 1, label: '加入照片' },
  { num: 2, label: '檢查例外' },
  { num: 3, label: '確認方案' },
  { num: 4, label: '執行整理' },
  { num: 5, label: '完成' },
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
