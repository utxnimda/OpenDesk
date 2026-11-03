<script setup lang="ts">
const emit = defineEmits<{ confirm: [] }>();

// API 1 frames block native form submission. Handle button activation before its default action.
function onClick(event: MouseEvent) {
  const form = event.currentTarget as HTMLFormElement;
  const button = (event.target as Element).closest<HTMLButtonElement>('button');
  if (!button || button.type !== 'submit' || button.form !== form || event.defaultPrevented) return;
  event.preventDefault();
  if (!button.disabled && form.reportValidity()) emit('confirm');
}

function onEnter(event: KeyboardEvent) {
  const input = event.target as HTMLInputElement;
  if (input.tagName !== 'INPUT' || !['text', 'url', 'email', 'password', 'search', 'tel'].includes(input.type) || event.defaultPrevented ||
      event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!event.repeat) {
    const form = event.currentTarget as HTMLFormElement;
    form.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
  }
}
</script>

<template>
  <form @click="onClick" @keydown.enter="onEnter" @submit.prevent><slot /></form>
</template>
