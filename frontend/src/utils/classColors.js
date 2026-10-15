export const CLASS_COLORS = [
  '#4299e0', '#e05252', '#52e088', '#e0c452',
  '#b452e0', '#52d4e0', '#e08a52', '#e052b4',
];

export function getClassColor(classIndex) {
  return CLASS_COLORS[(classIndex >= 0 ? classIndex : 0) % CLASS_COLORS.length];
}
