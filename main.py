import yaml
import argparse

import torch
import numpy as np

import gymnasium as gym
from gymnasium.wrappers import RecordEpisodeStatistics

from utils.misc import make_gif
from utils.logger import Logger
from utils.buffer import RolloutBuffer
from algos.svg_0_kl_prior.agent import SVG0 as SVG0_KL_prior
from algos.svg_0.agent import SVG0


def main(config, agent_cls):
    Logger.get().info(f"Start training, experiment name: {config['name']}")
    Logger.get().info(f"Config: {config}")

    env = RecordEpisodeStatistics(gym.make(config["env"]))
    test_env = RecordEpisodeStatistics(gym.make(config["env"], render_mode="rgb_array"))

    Logger.get().info(
        f"Env spaces: {env.observation_space, env.action_space}, steps: {env.spec.max_episode_steps}"
    )

    agent = agent_cls(
        obs_dim=env.observation_space.shape[0],
        action_dim=env.action_space.shape[0],
        action_lim=env.action_space.high[0],
        **config["svg0"],
    ).to(config["device"])

    buffer = RolloutBuffer(
        config["svg0"]["buffer_steps"],
        env.observation_space.shape[0],
        env.action_space.shape[0],
        config["device"],
    )

    global_step = 0
    for episode in range(1, config["epochs"]):
        obs, _ = env.reset()
        termination, truncated = False, False

        while not (termination or truncated):
            obs = torch.tensor(obs).to(config["device"])

            if global_step < config["svg0"]["buffer_steps"]:
                act = env.action_space.sample()
            else:
                act = agent.act(obs).cpu().numpy()

            next_obs, rew, termination, truncated, info = env.step(act)
            buffer.store(obs, act, rew, next_obs, termination)
            obs = next_obs

            if termination or truncated:
                break

            # Update on filled buffer and update check
            if (
                global_step % config["update_every_n"] == 0
                and global_step > config["svg0"]["buffer_steps"]
            ):
                batch = buffer.get()
                agent.optimize(batch, global_step)

            global_step += 1

        # Log final episode statistics
        Logger.get().writer.add_scalar("env/return", info["episode"]["r"], global_step)
        Logger.get().writer.add_scalar("env/length", info["episode"]["l"], global_step)

        # Store the weights, make a gif, eval and logging
        if episode % config["log_every_n"] == 0 and episode != 0:

            if episode % (config["log_every_n"] * 20) == 0:
                make_gif(agent, test_env, episode, config)

            # TODO: When implementation is stable
            # agent.save_weights(config["path"], epoch)

            test_return, test_ep_len = evaluate_policy(agent, test_env)

            Logger.get().info(
                f"episode #: {episode} "
                f"train - episode return, length: ({np.mean(info['episode']['r']):.3f}, "
                f" {np.mean(info['episode']['l']):.0f}) "
                f"test - episode return, length: ({np.mean(test_return):.3f}, "
                f"{np.mean(test_ep_len):.0f})"
            )


def evaluate_policy(agent, env, episodes=10):

    avg_return, avg_ep_len = [], []
    for _ in range(1, episodes):
        obs, _ = env.reset()
        termination, truncated = False, False

        while not (termination or truncated):
            obs = torch.tensor(obs).to(config["device"])
            act = agent.act(obs, deterministic=True)
            next_obs, _, termination, truncated, info = env.step(act.cpu().numpy())

            obs = next_obs

            if termination or truncated:
                avg_return.append(info["episode"]["r"])
                avg_ep_len.append(info["episode"]["l"])
                break

    return np.array(avg_return).mean(), np.array(avg_ep_len).mean()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--name", type=str)
    parser.add_argument(
        "-a", "--agent", type=str, default="svg0_prior", choices=["svg0", "svg0_prior"]
    )
    parser.add_argument("-c", "--config", type=str, default="configs/svg0.yml")
    args = parser.parse_args()
    assert args.name is not None, "Pass a name for the experiment"

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    # Initialize logger
    config["name"] = args.name
    config["path"] = f"runs/{args.name}"
    Logger(args.name, config["path"])

    # CUDA device
    config["device"] = torch.device(config["device_id"])
    torch.autograd.set_detect_anomaly(True)

    # Seed Numpy and Torch
    np.random.seed(config["seed"])
    torch.manual_seed(config["seed"])

    # Determine the agent
    agent_cls = SVG0 if args.agent == "svg0" else SVG0_KL_prior
    main(config, agent_cls=agent_cls)
