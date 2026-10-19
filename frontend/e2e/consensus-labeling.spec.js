import { expect, test } from '@playwright/test';

const project = {
  id: 1,
  name: 'Wildlife consensus',
  description: '',
  task_type: 'classification',
  class_list: ['cat', 'dog'],
  source_volume: '/Volumes/demo/images',
  source_type: 'volume',
  serving_endpoint: null,
  endpoint_config: null,
  consensus_enabled: true,
  consensus_panel_size: 3,
  consensus_coverage_percent: 100,
  consensus_quorum: 2,
  consensus_iou_threshold: 0.5,
  consensus_agreement_threshold: 0.5,
  consensus_reviewer_emails: [],
  consensus_role: 'labeller',
  created_by: 'owner@example.com',
  created_at: '2026-10-16T00:00:00Z',
  sample_count: 1,
  labeled_count: 0,
  version: 1,
  parent_project_id: null,
};

const sample = {
  id: 7,
  project_id: 1,
  filepath: '/Volumes/demo/images/cat.jpg',
  filename: 'cat.jpg',
  status: 'unlabeled',
  locked_by: null,
  locked_at: null,
  width: 16,
  height: 12,
  annotations: [],
  labels: [],
};

function taskFor(user) {
  const editing = user === 'user1@example.com';
  return {
    id: editing ? 101 : 202,
    case_id: 50,
    sample_id: sample.id,
    status: 'in_progress',
    assigned_to: user,
    lock_token: `lock-${user}`,
    locked_at: '2026-10-16T00:00:00Z',
    has_previous_submission: editing,
    editable_until_review: true,
    sample,
    annotations: editing
      ? [{ id: 1, label: 'cat', ann_type: 'classification' }]
      : [],
  };
}

async function mockApi(page, submissions, sessionUser) {
  await page.route('http://127.0.0.1:3000/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const user = request.headers()['x-forwarded-email'] || sessionUser;

    if (path === '/api/config') {
      return route.fulfill({ json: { db_backend: 'lakebase' } });
    }
    if (path === '/api/projects/1') {
      return route.fulfill({ json: project });
    }
    if (path === '/api/projects/1/stats') {
      return route.fulfill({
        json: {
          total: 1, labeled: 0, unlabeled: 1, skipped: 0,
          pre_labeled: 0, embedded: 0, per_user: [],
        },
      });
    }
    if (path === '/api/projects/1/endpoint-status') {
      return route.fulfill({ json: { status: 'not_configured' } });
    }
    if (path === '/api/projects/1/consensus/samples/7/claim') {
      return route.fulfill({ json: taskFor(user) });
    }
    if (path === '/api/projects/1/consensus/tasks/next') {
      return route.fulfill({
        json: {
          ...taskFor(user),
          id: 999,
          sample_id: 8,
          has_previous_submission: false,
          annotations: [],
          sample: {
            ...sample,
            id: 8,
            filepath: '/Volumes/demo/images/other.jpg',
            filename: 'other.jpg',
          },
        },
      });
    }
    if (path === '/api/projects/1/consensus/progress') {
      return route.fulfill({
        json: {
          total_tasks: 3,
          submitted_tasks: submissions.length,
          available_tasks: 1,
          my_submitted_tasks: 0,
          resolved_items: 0,
          review_items: 0,
          total_items: 1,
        },
      });
    }
    if (/\/api\/projects\/1\/consensus\/tasks\/\d+\/submit$/.test(path)) {
      submissions.push({
        user,
        taskId: Number(path.split('/').at(-2)),
        body: request.postDataJSON(),
      });
      return route.fulfill({
        json: {
          task_id: submissions.at(-1).taskId,
          case_id: 50,
          case_state: 'in_progress',
          submitted_labels: submissions.at(-1).body.annotations.map((row) => row.label),
          submitted_annotation_count: submissions.at(-1).body.annotations.length,
          resolution: null,
        },
      });
    }
    if (/\/api\/projects\/1\/consensus\/tasks\/\d+\/(heartbeat|release)$/.test(path)) {
      return route.fulfill({ json: { detail: 'OK' } });
    }
    if (path === '/api/projects/1/samples/7/image') {
      return route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="12"><rect width="16" height="12" fill="#888"/></svg>',
      });
    }
    return route.fulfill({
      status: 500,
      json: { detail: `Unmocked UI request: ${request.method()} ${path}` },
    });
  });
}

test('users independently label the same image and can revise before review lock', async ({ browser }) => {
  const submissions = [];

  const user1Context = await browser.newContext({
    extraHTTPHeaders: { 'X-Forwarded-Email': 'user1@example.com' },
  });
  const user1 = await user1Context.newPage();
  await mockApi(user1, submissions, 'user1@example.com');
  await user1.goto('/projects/1/label?sample=7');

  await expect(user1.getByTestId('consensus-editing-vote')).toContainText(
    'Editing your submitted vote',
  );
  await expect(user1.getByRole('button', { name: '✓ cat' })).toContainText('✓');
  await user1.getByRole('button', { name: '✓ cat' }).click();
  await user1.getByRole('button', { name: '2 dog' }).click();
  await user1.getByRole('button', { name: /Update vote \(dog\)/ }).click();
  await expect.poll(() => submissions.length).toBe(1);
  await expect(user1.getByText('cat.jpg', { exact: true })).toBeVisible();
  await expect(user1.getByText('other.jpg')).toHaveCount(0);
  expect(submissions[0]).toMatchObject({
    user: 'user1@example.com',
    taskId: 101,
    body: {
      annotations: [{ label: 'dog', ann_type: 'classification' }],
    },
  });

  const user2Context = await browser.newContext({
    extraHTTPHeaders: { 'X-Forwarded-Email': 'user2@example.com' },
  });
  const user2 = await user2Context.newPage();
  await mockApi(user2, submissions, 'user2@example.com');
  await user2.goto('/projects/1/label?sample=7');

  await expect(user2.getByText('cat.jpg', { exact: true })).toBeVisible();
  await expect(user2.getByTestId('consensus-editing-vote')).toHaveCount(0);
  await user2.getByRole('button', { name: '1 cat' }).click();
  await user2.getByRole('button', { name: /Save & Next \(cat\)/ }).click();
  await expect.poll(() => submissions.length).toBe(2);
  expect(submissions[1]).toMatchObject({
    user: 'user2@example.com',
    taskId: 202,
    body: {
      annotations: [{ label: 'cat', ann_type: 'classification' }],
    },
  });

  await user1Context.close();
  await user2Context.close();
});

test('adding a class does not re-claim the consensus detection task', async ({ page }) => {
  const liveProject = {
    ...project,
    task_type: 'detection',
    class_list: ['cat', 'dog'],
  };
  const claims = [];
  const task = {
    id: 303,
    case_id: 50,
    sample_id: sample.id,
    status: 'in_progress',
    assigned_to: 'user1@example.com',
    lock_token: 'lock-detection',
    locked_at: '2026-10-16T00:00:00Z',
    has_previous_submission: true,
    editable_until_review: true,
    sample,
    annotations: [{
      id: 1,
      label: 'cat',
      ann_type: 'bbox',
      bbox_json: { x: 0.1, y: 0.1, w: 0.4, h: 0.4 },
    }],
  };

  await page.route('http://127.0.0.1:3000/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/config') {
      return route.fulfill({ json: { db_backend: 'lakebase' } });
    }
    if (path === '/api/users/me') {
      return route.fulfill({
        json: { email: 'user1@example.com', display_name: 'User One' },
      });
    }
    if (path === '/api/projects/1') {
      return route.fulfill({ json: liveProject });
    }
    if (path === '/api/projects/1/stats') {
      return route.fulfill({
        json: {
          total: 1, labeled: 0, unlabeled: 1, skipped: 0,
          pre_labeled: 0, embedded: 0, per_user: [],
        },
      });
    }
    if (path === '/api/projects/1/endpoint-status') {
      return route.fulfill({ json: { status: 'not_configured' } });
    }
    if (path === '/api/projects/1/classes') {
      const body = request.postDataJSON();
      liveProject.class_list = [...liveProject.class_list, body.class_name];
      return route.fulfill({ json: { class_list: liveProject.class_list } });
    }
    if (path === '/api/projects/1/consensus/samples/7/claim') {
      claims.push(path);
      return route.fulfill({ json: task });
    }
    if (path === '/api/projects/1/consensus/tasks/next') {
      claims.push(path);
      return route.fulfill({ json: task });
    }
    if (path === '/api/projects/1/consensus/progress') {
      return route.fulfill({
        json: {
          total_tasks: 3,
          submitted_tasks: 1,
          available_tasks: 2,
          my_submitted_tasks: 1,
          resolved_items: 0,
          review_items: 0,
          total_items: 1,
        },
      });
    }
    if (/\/api\/projects\/1\/consensus\/tasks\/\d+\/(heartbeat|release)$/.test(path)) {
      return route.fulfill({ json: { detail: 'OK' } });
    }
    if (path === '/api/projects/1/samples/7/image') {
      return route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="12"><rect width="16" height="12" fill="#888"/></svg>',
      });
    }
    return route.fulfill({
      status: 500,
      json: { detail: `Unmocked UI request: ${request.method()} ${path}` },
    });
  });

  await page.goto('/projects/1/label?sample=7');
  await expect(page.getByText('cat.jpg', { exact: true })).toBeVisible();
  await expect.poll(() => claims.length).toBe(1);

  await page.getByPlaceholder('New class...').fill('bird');
  await page.getByRole('button', { name: '+ Add' }).click();
  await expect(page.getByRole('button', { name: /bird/ })).toBeVisible();
  await expect(page.getByTestId('consensus-editing-vote')).toBeVisible();
  expect(claims).toHaveLength(1);
});

test('hydrating a previous spatial vote selects that annotation class', async ({ page }) => {
  const liveProject = {
    ...project,
    task_type: 'segmentation',
    class_list: ['cat', 'dog'],
  };
  const task = {
    id: 404,
    case_id: 50,
    sample_id: sample.id,
    status: 'in_progress',
    assigned_to: 'user1@example.com',
    lock_token: 'lock-segmentation',
    locked_at: '2026-10-16T00:00:00Z',
    has_previous_submission: true,
    editable_until_review: true,
    sample: {
      ...sample,
      width: 16,
      height: 12,
    },
    annotations: [{
      id: 1,
      label: 'dog',
      ann_type: 'mask',
      mask_json: { size: [12, 16], counts: [0, 192] },
    }],
  };

  await page.route('http://127.0.0.1:3000/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/config') {
      return route.fulfill({ json: { db_backend: 'lakebase' } });
    }
    if (path === '/api/users/me') {
      return route.fulfill({
        json: { email: 'user1@example.com', display_name: 'User One' },
      });
    }
    if (path === '/api/projects/1') {
      return route.fulfill({ json: liveProject });
    }
    if (path === '/api/projects/1/stats') {
      return route.fulfill({
        json: {
          total: 1, labeled: 0, unlabeled: 1, skipped: 0,
          pre_labeled: 0, embedded: 0, per_user: [],
        },
      });
    }
    if (path === '/api/projects/1/endpoint-status') {
      return route.fulfill({ json: { status: 'not_configured' } });
    }
    if (path === '/api/projects/1/consensus/samples/7/claim') {
      return route.fulfill({ json: task });
    }
    if (path === '/api/projects/1/consensus/progress') {
      return route.fulfill({
        json: {
          total_tasks: 3,
          submitted_tasks: 1,
          available_tasks: 2,
          my_submitted_tasks: 1,
          resolved_items: 0,
          review_items: 0,
          total_items: 1,
        },
      });
    }
    if (/\/api\/projects\/1\/consensus\/tasks\/\d+\/(heartbeat|release)$/.test(path)) {
      return route.fulfill({ json: { detail: 'OK' } });
    }
    if (path === '/api/projects/1/samples/7/image') {
      return route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="12"><rect width="16" height="12" fill="#888"/></svg>',
      });
    }
    return route.fulfill({
      status: 500,
      json: { detail: `Unmocked UI request: ${request.method()} ${path}` },
    });
  });

  await page.goto('/projects/1/label?sample=7');
  await expect(page.getByText('Active: dog')).toBeVisible();
  await expect(page.getByTestId('consensus-editing-vote')).toBeVisible();
});
