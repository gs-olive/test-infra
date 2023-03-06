import _ from "lodash";
import { Octokit } from "octokit";
import { IssueData } from "./types";
import fetchIssuesByLabel from "lib/fetchIssuesByLabel";
import { isPyTorchPyTorch } from "./bot/utils";

export const NUM_MINUTES = 30;
export const REPO: string = "pytorch";
export const OWNER: string = "pytorch";
export const DRCI_COMMENT_START = "<!-- drci-comment-start -->\n";
export const OH_URL =
    "https://github.com/pytorch/pytorch/wiki/Dev-Infra-Office-Hours";
export const DOCS_URL = "https://docs-preview.pytorch.org/";
export const PYTHON_DOCS_URL = "/index.html";
export const CPP_DOCS_URL = "/cppdocs/index.html";
export const DRCI_COMMENT_END = `\n
This comment was automatically generated by Dr. CI and updates every 15 minutes.
<!-- drci-comment-end -->`;
export const HUD_URL = "https://hud.pytorch.org/pr/";
export const BOT_COMMANDS_WIKI_URL =
  "https://github.com/pytorch/pytorch/wiki/Bot-commands";
export const FLAKY_RULES_JSON =
  "https://raw.githubusercontent.com/pytorch/test-infra/generated-stats/stats/flaky-rules.json";

export function formDrciHeader(prNum: number): string {
    return `## :link: Helpful Links
### :test_tube: See artifacts and rendered test results at [hud.pytorch.org/pr/${prNum}](${HUD_URL}${prNum})
* :page_facing_up: Preview [Python docs built from this PR](${DOCS_URL}${prNum}${PYTHON_DOCS_URL})
* :page_facing_up: Preview [C++ docs built from this PR](${DOCS_URL}${prNum}${CPP_DOCS_URL})
* :question: Need help or want to give feedback on the CI? Visit the [bot commands wiki](${BOT_COMMANDS_WIKI_URL}) or our [office hours](${OH_URL})

Note: Links to docs will display an error until the docs builds have been completed.`;
}

export function formDrciComment(
  pr_num: number,
  pr_results: string = "",
  sevs: string = ""
): string {
  const header = formDrciHeader(pr_num);
  const comment = `${DRCI_COMMENT_START}
${header}
${sevs}
${pr_results}
${DRCI_COMMENT_END}`;
  return comment;
}


export async function getDrciComment(
  octokit: Octokit,
  owner: string,
  repo: string,
  prNum: number
): Promise<{ id: number; body: string }> {
  const commentsRes = await octokit.rest.issues.listComments({
    owner: owner,
    repo: repo,
    issue_number: prNum,
  });
  for (const comment of commentsRes.data) {
    if (comment.body!.includes(DRCI_COMMENT_START)) {
      return { id: comment.id, body: comment.body! };
    }
  }
  return { id: 0, body: "" };
}

export function getActiveSEVs(issues: IssueData[]): [IssueData[], IssueData[]] {
  const activeSEVs = issues.filter(
    (issue: IssueData) => issue.state === "open"
  );
  return _.partition(activeSEVs, (issue: IssueData) =>
    issue.body.toLowerCase().includes("merge blocking")
  );
}

export function formDrciSevBody(sevs: [IssueData[], IssueData[]]): string {
  const [mergeBlocking, notMergeBlocking] = sevs;
  if (mergeBlocking.length + notMergeBlocking.length === 0) {
    return "";
  }
  const sev_list = mergeBlocking
    .concat(notMergeBlocking)
    .map(
      (issue: IssueData) =>
        `* ${
          issue.body.toLowerCase().includes("merge blocking")
            ? "(merge blocking) "
            : ""
        }[${issue.title}](${issue.html_url.replace(
          "github.com",
          "hud.pytorch.org"
        )})`
    )
    .join("\n");
  if (mergeBlocking.length > 0) {
    return (
      `## :heavy_exclamation_mark: ${mergeBlocking.length} Merge Blocking SEVs
There is ${mergeBlocking.length} active merge blocking SEVs` +
      (notMergeBlocking.length > 0
        ? ` and ${notMergeBlocking.length} non merge blocking SEVs`
        : "") +
      `.  Please view them below:
${sev_list}\n
If you must merge, use \`@pytorchbot merge -f\`.`
    );
  } else {
    return `## :heavy_exclamation_mark: ${notMergeBlocking.length} Active SEVs
There are ${notMergeBlocking.length} currently active SEVs.   If your PR is affected, please view them below:
${sev_list}\n
`;
  }
}

// The context here is the context from probot.
// Today we only use probot for upserts, but this could later be split into logger
export async function upsertDrCiComment(owner: string, repo: string, prNum: number, context: any, prUrl: string) {
  // Dr.CI only supports [pytorch/pytorch, pytorch/vision] at the moment
  if (!isPyTorchPyTorch(owner, repo)) {
    context.log(`Pull request to ${owner}/${repo} is not supported by Dr.CI bot, no comment is made`);
    return;
  }

  const existingDrciData = await getDrciComment(
    context.octokit,
    owner,
    repo,
    prNum
  );
  context.log("Got existing ID: " + existingDrciData.id + " with body " + existingDrciData.body)
  const existingDrciID = existingDrciData.id;
  const existingDrciComment = existingDrciData.body;
  const sev = getActiveSEVs(await fetchIssuesByLabel("ci: sev"));
  const drciComment = formDrciComment(prNum, "", formDrciSevBody(sev));

  if (existingDrciComment === drciComment) {
    return;
  }

  if (existingDrciID === 0) {
    await context.octokit.issues.createComment({
      body: drciComment,
      owner: owner,
      repo: repo,
      issue_number: prNum,
    });
    context.log(
      `Commenting with "${drciComment}" for pull request ${prUrl}`
    );
  } else {
    context.log({
      body: drciComment,
      owner: owner,
      repo: repo,
      comment_id: existingDrciID,
    });
    await context.octokit.issues.updateComment({
      body: drciComment,
      owner: owner,
      repo: repo,
      comment_id: existingDrciID,
    });
    context.log(
      `Updated comment with "${drciComment}" for pull request ${prUrl}`
    );
  }
}
