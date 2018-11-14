# Copyright 2018 The RLgraph authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import unittest

from rlgraph.components.policies.policy import Policy
from rlgraph.spaces import *
from rlgraph.tests import ComponentTest
from rlgraph.tests.test_util import config_from_path
from rlgraph.utils import softmax, relu


class TestPolicies(unittest.TestCase):

    def test_policy_for_discrete_action_space(self):
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        state_space = FloatBox(shape=(4,), add_batch_rank=True)

        # action_space (5 possible actions).
        action_space = IntBox(5, add_batch_rank=True)

        policy = Policy(network_spec=config_from_path("configs/test_simple_nn.json"), action_space=action_space)
        test = ComponentTest(
            component=policy,
            input_spaces=dict(
                nn_input=state_space,
                actions=action_space
            ),
            action_space=action_space
        )
        policy_params = test.read_variable_values(policy.variables)

        # Some NN inputs (4 input nodes, batch size=2).
        states = np.array([[-0.08, 0.4, -0.05, -0.55], [13.0, -14.0, 10.0, -16.0]])
        # Raw NN-output.
        expected_nn_output = np.matmul(states, policy_params["policy/test-network/hidden-layer/dense/kernel"])
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=6)

        # Raw action layer output; Expected shape=(2,5): 2=batch, 5=action categories
        expected_action_layer_output = np.matmul(
            expected_nn_output, policy_params["policy/action-adapter-0/action-layer/dense/kernel"]
        )
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_output),
                  decimals=5)

        # Logits, parameters (probs) and skip log-probs (numerically unstable for small probs).
        expected_probabilities_output = softmax(expected_action_layer_output, axis=-1)
        test.test(("get_logits_probabilities_log_probs", states, [0, 1]), expected_outputs=dict(
            logits=expected_action_layer_output, probabilities=np.array(expected_probabilities_output, dtype=np.float32)
        ), decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        expected_actions = np.argmax(expected_action_layer_output, axis=-1)
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)
        self.assertTrue(out["action"].shape == (2,))

        # Deterministic sample.
        test.test(("get_deterministic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)
        self.assertTrue(out["action"].shape == (2,))

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)  # dict(entropy=expected_h), decimals=3)
        self.assertTrue(out["entropy"].dtype == np.float32)
        self.assertTrue(out["entropy"].shape == (2,))

        # Action log-probs.
        expected_action_log_prob_output = dict(action_log_probs=np.log(np.array([
            expected_probabilities_output[0][expected_actions[0]],
            expected_probabilities_output[1][expected_actions[1]],
        ])))
        test.test(("get_action_log_probs", [states, expected_actions]),
                  expected_outputs=expected_action_log_prob_output, decimals=5)

    def test_policy_for_discrete_action_space_with_dueling_layer(self):
        np.random.seed(10)
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        nn_input_space = FloatBox(shape=(3,), add_batch_rank=True)

        # action_space (2 possible actions).
        action_space = IntBox(2, add_batch_rank=True)

        # Policy with additional dueling layer.
        policy = Policy(
            network_spec=config_from_path("configs/test_lrelu_nn.json"),
            action_adapter_spec=dict(
                type="dueling-action-adapter", action_space=action_space,
                units_state_value_stream=10,
                units_advantage_stream=10
            ),
        )
        test = ComponentTest(
            component=policy,
            input_spaces=dict(
                nn_input=nn_input_space,
                actions=action_space
            ),
            action_space=action_space
        )
        policy_params = test.read_variable_values(policy.variables)

        # Some NN inputs (3 input nodes, batch size=3).
        nn_input = nn_input_space.sample(size=3)
        # Raw NN-output (3 hidden nodes). All weights=1.5, no biases.
        expected_nn_output = relu(np.matmul(nn_input, policy_params["policy/test-network/hidden-layer/dense/kernel"]),
                                  0.1)
        test.test(("get_nn_output", nn_input), expected_outputs=dict(output=expected_nn_output))

        # Raw action layer output; Expected shape=(3,3): 3=batch, 2=action categories + 1 state value
        expected_state_value = np.matmul(relu(np.matmul(
            expected_nn_output,
            policy_params["policy/dueling-action-adapter-0/dense-layer-state-value-stream/dense/kernel"]
        )), policy_params["policy/dueling-action-adapter-0/state-value-node/dense/kernel"])
        expected_raw_advantages = np.matmul(relu(np.matmul(
            expected_nn_output, policy_params["policy/dueling-action-adapter-0/dense-layer-advantage-stream/dense/kernel"]
        )), policy_params["policy/dueling-action-adapter-0/action-layer/dense/kernel"])
        test.test(("get_action_layer_output", nn_input), expected_outputs=dict(
            state_value_node=expected_state_value, output=expected_raw_advantages
        ), decimals=5)

        # State-values: One for each item in the batch (simply take first out-node of action_layer).
        # Advantage-values: One for each action-choice per item in the batch (simply take second and third out-node
        # Q-values: One for each action-choice per item in the batch (calculate from state-values and advantage-values
        expected_q_values_output = expected_state_value + expected_raw_advantages - \
            np.mean(expected_raw_advantages, axis=-1, keepdims=True)
        test.test(("get_logits_probabilities_log_probs", nn_input, ["state_values", "logits"]), expected_outputs=dict(
            state_values=expected_state_value, logits=expected_q_values_output
        ), decimals=5)

        expected_actions = np.argmax(expected_q_values_output, axis=-1)
        test.test(("get_action", nn_input), expected_outputs=dict(action=expected_actions))

        # Parameter (probabilities). Softmaxed q_values.
        expected_probabilities_output = softmax(expected_q_values_output, axis=-1)
        test.test(("get_logits_probabilities_log_probs", nn_input, ["logits", "probabilities"]), expected_outputs=dict(
            logits=expected_q_values_output,
            probabilities=expected_probabilities_output
        ), decimals=5)

        # Action log-probs.
        expected_action_log_prob_output = np.log(np.array([
            expected_probabilities_output[0][expected_actions[0]],
            expected_probabilities_output[1][expected_actions[1]],
            expected_probabilities_output[2][expected_actions[2]],
        ]))
        test.test(("get_action_log_probs", [nn_input, expected_actions]),
                  expected_outputs=expected_action_log_prob_output, decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        # Stochastic sample.
        out = test.test(("get_stochastic_action", nn_input), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)

        # Deterministic sample.
        out = test.test(("get_deterministic_action", nn_input), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)

        # Distribution's entropy.
        out = test.test(("get_entropy", nn_input), expected_outputs=None)  # dict(entropy=expected_h), decimals=3)
        self.assertTrue(out["entropy"].dtype == np.float32)

    def test_policy_for_discrete_action_space_with_baseline_layer(self):
        np.random.seed(11)
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        state_space = FloatBox(shape=(4,), add_batch_rank=True)

        # action_space (3 possible actions).
        action_space = IntBox(3, add_batch_rank=True)

        # Policy with baseline action adapter.
        policy = Policy(
            network_spec=config_from_path("configs/test_lrelu_nn.json"),
            action_adapter_spec=dict(type="baseline_action_adapter", action_space=action_space)
        )
        test = ComponentTest(
            component=policy,
            input_spaces=dict(
                nn_input=state_space,
                actions=action_space
            ),
            action_space=action_space,
            seed=11
        )
        policy_params = test.read_variable_values(policy.variables)

        # Some NN inputs (4 input nodes, batch size=3).
        states = state_space.sample(size=3)
        # Raw NN-output (3 hidden nodes). All weights=1.5, no biases.
        expected_nn_output = np.matmul(states, policy_params["policy/test-network/hidden-layer/dense/kernel"])
        expected_nn_output = relu(expected_nn_output, 0.1)
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=5)

        # Raw action layer output; Expected shape=(3,3): 3=batch, 2=action categories + 1 state value
        expected_action_layer_output = np.matmul(
            expected_nn_output, policy_params["policy/baseline-action-adapter-0/action-layer/dense/kernel"]
        )
        expected_action_layer_output = np.reshape(expected_action_layer_output, newshape=(3, 4))
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_output),
                  decimals=5)

        # State-values: One for each item in the batch (simply take first out-node of action_layer).
        expected_state_value_output = expected_action_layer_output[:, :1]
        # logits-values: One for each action-choice per item in the batch (simply take the remaining out nodes).
        expected_logits_output = expected_action_layer_output[:, 1:]
        test.test(("get_state_values_logits_probabilities_log_probs", states, ["state_values", "logits"]),
                  expected_outputs=dict(state_values=expected_state_value_output, logits=expected_logits_output),
                  decimals=5)

        expected_actions = np.argmax(expected_logits_output, axis=-1)
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Parameter (probabilities). Softmaxed logits.
        expected_probabilities_output = softmax(expected_logits_output, axis=-1)
        test.test(("get_logits_probabilities_log_probs", states, ["logits", "probabilities"]), expected_outputs=dict(
            logits=expected_logits_output,
            probabilities=expected_probabilities_output
        ), decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)

        # Deterministic sample.
        out = test.test(("get_deterministic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)  # dict(entropy=expected_h), decimals=2)
        self.assertTrue(out["entropy"].dtype == np.float32)

    def test_policy_for_discrete_action_space_with_baseline_layer_with_time_rank_folding(self):
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        state_space = FloatBox(shape=(3,), add_batch_rank=True, add_time_rank=True)

        # action_space (4 possible actions).
        action_space = IntBox(4, add_batch_rank=True, add_time_rank=True)

        # Policy with baseline action adapter AND batch-apply over the entire policy (NN + ActionAdapter + distr.).
        policy = Policy(
            network_spec=config_from_path("configs/test_lrelu_nn.json"),
            action_adapter_spec=dict(type="baseline_action_adapter", action_space=action_space),
            batch_apply=True
        )
        test = ComponentTest(
            component=policy,
            input_spaces=dict(nn_input=state_space, actions=action_space),
            action_space=action_space,
        )
        policy_params = test.read_variable_values(policy.variables)

        # Some NN inputs.
        states = state_space.sample(size=(2, 3))
        states_folded = np.reshape(states, newshape=(6, 3))
        # Raw NN-output (3 hidden nodes). All weights=1.5, no biases.
        expected_nn_output = np.matmul(states_folded, policy_params["policy/test-network/hidden-layer/dense/kernel"])
        expected_nn_output = relu(expected_nn_output, 0.1)
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=5)

        # Raw action layer output; Expected shape=(3,3): 3=batch, 2=action categories + 1 state value
        expected_action_layer_output = np.matmul(
            expected_nn_output, policy_params["policy/baseline-action-adapter-0/action-layer/dense/kernel"]
        )
        expected_action_layer_output = np.reshape(expected_action_layer_output, newshape=(6, 4+1))
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_output),
                  decimals=5)

        expected_action_layer_output_unfolded = np.reshape(expected_action_layer_output, newshape=(2, 3, 4+1))
        # State-values: One for each item in the batch (simply take first out-node of action_layer).
        expected_state_value_output = expected_action_layer_output_unfolded[:, :, :1]
        # logits-values: One for each action-choice per item in the batch (simply take the remaining out nodes).
        expected_logits_output = expected_action_layer_output_unfolded[:, :, 1:]
        test.test(("get_state_values_logits_probabilities_log_probs", states, ["state_values", "logits"]),
                  expected_outputs=dict(state_values=expected_state_value_output, logits=expected_logits_output),
                  decimals=5)

        test.test(("get_logits_probabilities_log_probs", states, ["logits"]),
                  expected_outputs=dict(logits=expected_logits_output),
                  decimals=5)

        expected_actions = np.argmax(expected_logits_output, axis=-1)
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Parameter (probabilities). Softmaxed logits.
        expected_probabilities_output = softmax(expected_logits_output, axis=-1)
        test.test(("get_logits_probabilities_log_probs", states, ["logits", "probabilities"]), expected_outputs=dict(
            logits=expected_logits_output,
            probabilities=expected_probabilities_output
        ), decimals=5)

        # Action log-probs.
        expected_action_log_prob_output = np.log(np.array([[
            expected_probabilities_output[0][0][expected_actions[0][0]],
            expected_probabilities_output[0][1][expected_actions[0][1]],
            expected_probabilities_output[0][2][expected_actions[0][2]],
        ], [
            expected_probabilities_output[1][0][expected_actions[1][0]],
            expected_probabilities_output[1][1][expected_actions[1][1]],
            expected_probabilities_output[1][2][expected_actions[1][2]],
        ]]))
        test.test(("get_action_log_probs", [states, expected_actions]),
                  expected_outputs=expected_action_log_prob_output, decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        # Deterministic sample.
        out = test.test(("get_deterministic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)
        self.assertTrue(out["action"].shape == (2, 3))  # Make sure output is unfolded.

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"].dtype == np.int32)
        self.assertTrue(out["action"].shape == (2, 3))  # Make sure output is unfolded.

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)  # dict(entropy=expected_h), decimals=2)
        self.assertTrue(out["entropy"].dtype == np.float32)
        self.assertTrue(out["entropy"].shape == (2, 3))  # Make sure output is unfolded.

