const assert = require('node:assert/strict');
const test = require('node:test');
const resources = require('../serverless/services/automation-resources')('prod');
const definition = JSON.parse(resources.Resources.AutomationWorkflowStateMachine.Properties.DefinitionString['Fn::Sub'][0]);
const states = definition.States;
const resolve = (input, path) => path.slice(2).split('.').reduce((value, key) => value[key], input);

for (const scenario of ['recommended', 'alternative', 'custom']) {
  test(`successful case retains executed ${scenario} plan`, () => {
    const recommended = { steps: [{ command: 'echo recommended' }] };
    const chosen = scenario === 'recommended' ? recommended : {
      source: scenario,
      steps: [{ command: `echo ${scenario}` }],
      risk_level: 'basic',
    };
    const input = { plan: { plan: recommended }, approval: { selected_plan: chosen } };
    const executed = resolve(input, states.ExecuteTicketPlan.Parameters['plan.$']);
    const recorded = resolve(input, states.RegisterSuccessfulCase.Parameters['plan_used.$']);
    assert.deepEqual(recorded, executed);
    assert.deepEqual(recorded, chosen);
    if (scenario !== 'recommended') assert.notDeepEqual(recorded, recommended);
  });
}

test('all state transitions target existing states', () => {
  assert.ok(states[definition.StartAt]);
  for (const [name, state] of Object.entries(states)) {
    const targets = [state.Next, state.Default,
      ...(state.Choices || []).map(choice => choice.Next),
      ...(state.Catch || []).map(handler => handler.Next)].filter(Boolean);
    for (const target of targets) assert.ok(states[target], `${name} -> ${target}`);
  }
});
