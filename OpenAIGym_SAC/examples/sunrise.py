import sys
from pathlib import Path

# Add the parent directory of 'examples' (i.e., OpenAIGym_SAC) to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import argparse
import rlkit.torch.pytorch_util as ptu

from rlkit.data_management.env_replay_buffer import DynamicEnsembleEnvReplayBuffer
from rlkit.envs.wrappers import NormalizedBoxEnv
from rlkit.launchers.launcher_util import setup_logger_custom, set_seed
from rlkit.samplers.data_collector import EnsembleMdpPathCollector
from rlkit.torch.sac.neurips20_sac_ensemble import NeurIPS20SACEnsembleTrainer
from rlkit.torch.torch_rl_algorithm import DynamicTorchBatchRLAlgorithm

import gymnasium as gym

from examples.sunrise_ensemble import Ensemble

def parse_args():
    parser = argparse.ArgumentParser()
    # architecture
    parser.add_argument('--num_layer', default=2, type=int)
    
    # train
    parser.add_argument('--batch_size', default=256, type=int)
    parser.add_argument('--save_freq', default=0, type=int)
    parser.add_argument('--computation_device', default='cpu', type=str)
    parser.add_argument('--epochs', default=210, type=int)

    # misc
    parser.add_argument('--seed', default=1, type=int)
    parser.add_argument('--exp_dir', default='data', type=str)
    parser.add_argument('--exp_name', default='experiment', type=str)
    
    # env
    parser.add_argument('--env', default="Ant-v5", type=str)
    
    # ensemble
    parser.add_argument('--num_ensemble', default=3, type=int)
    parser.add_argument('--ber_mean', default=0.5, type=float)
    
    # inference
    parser.add_argument('--inference_type', default=0.0, type=float)
    
    # corrective feedback
    parser.add_argument('--temperature', default=20.0, type=float)
    parser.add_argument('--removal_check_buffer_size', default=2000, type=int)
    parser.add_argument('--removal_check_frequency', default=10000, type=int)
    
    args = parser.parse_args()
    return args


def experiment(variant):
    expl_env = NormalizedBoxEnv(gym.make(variant['env']))
    eval_env = NormalizedBoxEnv(gym.make(variant['env']))
    obs_dim = expl_env.observation_space.shape[0]
    action_dim = eval_env.action_space.shape[0]
    
    M = variant['layer_size']
    num_layer = variant['num_layer']
    network_structure = [M] * num_layer
    
    NUM_ENSEMBLE = variant['num_ensemble']

    ensemble = Ensemble(
        NUM_ENSEMBLE,
        expl_env.observation_space,
        expl_env.action_space,
        network_structure,
        # These parameters are not used but just added to allow it to run.
        diversity_threshold=variant.get('diversity_threshold', 0.006),
        diversity_critical_threshold=variant.get('diversity_critical_threshold', 0.005),
        performance_gamma=variant.get('performance_gamma', 0.95),
        window_size=variant.get('window_size', 1000),
        noise=variant.get('noise', 0.1),
        retrain_steps=variant.get('retrain_steps', 0),
    )
    
    eval_path_collector = EnsembleMdpPathCollector(
        eval_env,
        ensemble.get_eval_policies(),
        NUM_ENSEMBLE,
        eval_flag=True,
    )
    
    expl_path_collector = EnsembleMdpPathCollector(
        expl_env,
        ensemble.get_policies(),
        NUM_ENSEMBLE,
        ber_mean=variant['ber_mean'],
        eval_flag=False,
        critic1=ensemble.get_critic1s(),
        critic2=ensemble.get_critic2s(),
        inference_type=variant['inference_type'],
        feedback_type=1,
    )
    
    # Have to switch over to the newer dynamic version to allow for the get historic performance functions.
    replay_buffer = DynamicEnsembleEnvReplayBuffer(
        variant['replay_buffer_size'],
        expl_env,
        NUM_ENSEMBLE,
        log_dir=variant['log_dir'],
    )
    
    trainer = NeurIPS20SACEnsembleTrainer(
        env=eval_env,
        policy=ensemble.get_policies(),
        qf1=ensemble.get_critic1s(),
        qf2=ensemble.get_critic2s(),
        target_qf1=ensemble.get_target_critic1s(),
        target_qf2=ensemble.get_target_critic2s(),
        num_ensemble=NUM_ENSEMBLE,
        feedback_type=1,
        temperature=variant['temperature'],
        temperature_act=0,
        expl_gamma=0,
        log_dir=variant['log_dir'],
        **variant['trainer_kwargs']
    )
    algorithm = DynamicTorchBatchRLAlgorithm(
        trainer=trainer,
        exploration_env=expl_env,
        evaluation_env=eval_env,
        exploration_data_collector=expl_path_collector,
        evaluation_data_collector=eval_path_collector,
        replay_buffer=replay_buffer,
        # Extra arguments to allow for diversity checking throughout the learning process
        ensemble=ensemble,
        always_dryrun=True,
        **variant['algorithm_kwargs']
    )
    
    algorithm.to(ptu.device)
    algorithm.train()


if __name__ == "__main__":
    args = parse_args()
    
    # noinspection PyTypeChecker
    variant = dict(
        algorithm="SAC",
        version="normal",
        layer_size=256,
        replay_buffer_size=int(1E6),
        algorithm_kwargs=dict(
            num_epochs=args.epochs,
            num_eval_steps_per_epoch=1000,
            num_trains_per_train_loop=1000,
            num_expl_steps_per_train_loop=1000,
            min_num_steps_before_training=1000,
            max_path_length=1000,
            batch_size=args.batch_size,
            save_frequency=args.save_freq,
            removal_check_frequency=args.removal_check_frequency,
            removal_check_buffer_size=args.removal_check_buffer_size
        ),
        trainer_kwargs=dict(
            discount=0.99,
            soft_target_tau=5e-3,
            target_update_period=1,
            policy_lr=3E-4,
            qf_lr=3E-4,
            reward_scale=1,
            use_automatic_entropy_tuning=True,
        ),
        num_ensemble=args.num_ensemble,
        num_layer=args.num_layer,
        seed=args.seed,
        ber_mean=args.ber_mean,
        env=args.env,
        inference_type=args.inference_type,
        temperature=args.temperature,
    )
                            
    set_seed(args.seed)
    exp_name = args.exp_name
    log_dir = setup_logger_custom(exp_name, log_dir=args.exp_dir, variant=variant)

    variant['log_dir'] = log_dir
    if 'cuda' in args.computation_device:
        ptu.set_gpu_mode(True, gpu_id=args.computation_device[0])
    else:
        ptu.set_gpu_mode(False)
    experiment(variant)